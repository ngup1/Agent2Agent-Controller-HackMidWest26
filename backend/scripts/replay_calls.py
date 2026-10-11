"""Replay team recordings through application routes; never insert staff decisions."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid5

import httpx

from backend.config import Settings
from backend.database.replay import ReplayRepository
from backend.services.conversation_tokens import conversation_token_for


@dataclass(frozen=True)
class RecordedCall:
    id: str
    category: str
    caller_turns: tuple[str, ...]
    outcome: str | None
    transcript: tuple[dict, ...]

    @property
    def reference(self):
        content = json.dumps({"transcript": self.transcript, "outcome": self.outcome}, sort_keys=True)
        digest = hashlib.sha256(content.encode()).hexdigest()
        return f"ssa008-replay:{self.id}:{digest}"


def load_recordings(directory: Path) -> list[RecordedCall]:
    recordings = []
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text())
        if data.get("source") != "team-recording":
            raise ValueError(f"{path.name}: source must be team-recording")
        identifier = data.get("id", "")
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", identifier):
            raise ValueError(f"{path.name}: invalid recording id")
        if data.get("category") not in {"routine", "large-order", "allergy", "manipulation"}:
            raise ValueError(f"{path.name}: invalid category")
        if "outcome" not in data or data["outcome"] not in {None, "approved", "denied"}:
            raise ValueError(f"{path.name}: outcome must be approved, denied, or null")
        transcript = data.get("transcript")
        if not isinstance(transcript, list) or not transcript:
            raise ValueError(f"{path.name}: transcript is required")
        for segment in transcript:
            if (not isinstance(segment, dict) or segment.get("speaker") not in {"caller", "business_agent", "human"}
                    or not isinstance(segment.get("content"), str) or not segment["content"].strip()):
                raise ValueError(f"{path.name}: invalid transcript segment")
        turns = tuple(s["content"] for s in transcript if s["speaker"] == "caller")
        if any(len(turn) > 4000 for turn in turns):
            raise ValueError(f"{path.name}: each caller turn must contain at most 4000 characters")
        if not turns or len("\n".join(turns)) > 20000:
            raise ValueError(f"{path.name}: caller text must contain 1-20000 characters")
        recordings.append(RecordedCall(identifier, data["category"], turns, data["outcome"], tuple(transcript)))
    if not recordings:
        raise ValueError("No team recordings found; record calls before replay/calibration")
    if len({r.id for r in recordings}) != len(recordings):
        raise ValueError("Recording ids must be unique")
    return recordings


class HttpReplayTransport:
    """Trusted local replay using the app's business and session secret.

    ReplayRepository reserves a stable API interaction in the app's database.
    Sign its token using the same helper as /chat; do not bypass route validation.
    """

    def __init__(self, client: httpx.Client, business_id, secret: str):
        if len(secret) < 24:
            raise ValueError("Replay requires the app's CHAT_SESSION_SECRET (24+ characters)")
        self.client, self.business_id, self.secret = client, business_id, secret

    def turn(self, interaction_id, text):
        token = conversation_token_for(self.secret, self.business_id, interaction_id)
        response = self.client.post("/chat", json={
            "interaction_id": str(interaction_id), "conversation_token": token, "text": text,
        })
        response.raise_for_status()

    def decide(self, action, outcome):
        path = f"/dashboard/cases/{action['id']}/decision"
        response = self.client.post(path, json={"decision": "approve" if outcome == "approved" else "deny"})
        response.raise_for_status()


def _complete(state, recording):
    if state is None or state["reference"] != recording.reference or state["turns"] != list(recording.caller_turns):
        return False
    if len(state["actions"]) != 1:
        return False
    action = state["actions"][0]
    expected_state = "rejected" if recording.outcome == "denied" else "executed"
    return action["outcome"] == recording.outcome and action["state"] == expected_state


def replay_call(recording, business_id, repository, transport):
    interaction_id = uuid5(business_id, f"ssa008-replay:{recording.id}")
    state = repository.state(business_id, interaction_id)
    if _complete(state, recording):
        return "skipped"
    if state is not None:
        raise RuntimeError(f"{recording.id}: existing replay is incomplete or changed; inspect it before retrying")
    if not repository.start(business_id, interaction_id, recording.reference):
        raise RuntimeError(f"{recording.id}: replay already started")
    for text in recording.caller_turns:
        transport.turn(interaction_id, text)
    state = repository.state(business_id, interaction_id)
    if state is None or state["turns"] != list(recording.caller_turns) or len(state["actions"]) != 1:
        raise RuntimeError(f"{recording.id}: chat did not persist the expected turns and one proposal")
    if recording.outcome is not None:
        action = state["actions"][0]
        if action["outcome"] != "pending" or action["state"] != "pending_review" or not action["review_id"]:
            raise RuntimeError(f"{recording.id}: expected a pending staff review; replay will not fabricate one")
        transport.decide(action, recording.outcome)
    if not _complete(repository.state(business_id, interaction_id), recording):
        raise RuntimeError(f"{recording.id}: application did not persist the expected outcome")
    return "replayed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("fixtures/calls"))
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    settings = Settings.from_env()
    try:
        recordings = load_recordings(args.directory)
        password = os.getenv("REPLAY_DASHBOARD_PASSWORD") or settings.dashboard_password
        if any(call.outcome is not None for call in recordings) and not password:
            raise ValueError("Replay of reviewed calls requires DASHBOARD_PASSWORD or REPLAY_DASHBOARD_PASSWORD")
        auth = httpx.BasicAuth("staff", password) if password else None
        with httpx.Client(base_url=args.base_url, auth=auth, timeout=60) as client:
            transport = HttpReplayTransport(client, settings.business_id, settings.chat_session_secret)
            repository = ReplayRepository()
            for recording in recordings:
                print(f"{recording.id}: {replay_call(recording, settings.business_id, repository, transport)}")
    except (ValueError, RuntimeError, httpx.HTTPError) as exc:
        # Never print HTTP bodies or credentials.
        if isinstance(exc, httpx.HTTPError):
            parser.exit(1, f"Replay unavailable: {type(exc).__name__}; inspect application state before retrying\n")
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
