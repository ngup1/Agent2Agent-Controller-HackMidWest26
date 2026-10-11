"""Real chat, gate, review, booking and pgvector; only inference is fake."""
from dataclasses import replace
from uuid import uuid5

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import httpx as test_httpx

from backend.config import DEMO_BUSINESS_ID, Settings
from backend.database.replay import ReplayRepository
from backend.main import create_app
from backend.scripts.replay_calls import HttpReplayTransport, RecordedCall, replay_call
from backend.scripts.seed import seed
from backend.services.booking_chat import ChatTurn
from backend.services.conversation import CALLBACK_LINE
from backend.services import gateway
from tests.integration.test_foundation import PROPOSAL

pytestmark = pytest.mark.integration
SETTINGS = Settings(retrieval_mode="fake", jev_mode="fake",
                    chat_session_secret="test-only-long-replay-session-secret",
                    dashboard_password="test-only-staff-password")


class ScriptedChat:
    """Explicit fake model; the actual conversation service confirms proposals."""
    def __init__(self, proposal):
        self.proposal = proposal

    async def generate(self, turns):
        return ChatTurn(proposal=self.proposal)


def app_client(database, settings=SETTINGS, party=1):
    seed(database.conn, DEMO_BUSINESS_ID)
    start = database.conn.execute(
        "SELECT start_time FROM slots WHERE start_time>now()+interval '1 day' ORDER BY start_time LIMIT 1"
    ).fetchone()[0].isoformat()
    return TestClient(create_app(
        settings, connection_factory=database.connect,
        chat=ScriptedChat(PROPOSAL | {"start_time": start, "party_size": party}),
    ))


def call_recording(identifier, outcome, text="Book two haircuts tomorrow for our group"):
    turns = (text, "Yes")
    return RecordedCall(identifier, "group", turns, outcome,
                        tuple({"speaker": "caller", "content": t} for t in turns))


def state_for(database, recording):
    interaction = uuid5(DEMO_BUSINESS_ID, f"ssa008-replay:{recording.id}")
    return ReplayRepository(database.connect).state(DEMO_BUSINESS_ID, interaction)


def transport(client, settings=SETTINGS):
    client.auth = ("staff", settings.dashboard_password)
    return HttpReplayTransport(client, settings.business_id, settings.chat_session_secret)


@pytest.mark.parametrize("outcome", ["approved", "denied", None])
def test_replay_twice_through_real_app_preserves_one_call_and_outcome(database, outcome):
    recording = call_recording("integration-test", outcome)
    repository = ReplayRepository(database.connect)
    with app_client(database, party=1 if outcome is None else 2) as client:
        http = transport(client)
        assert replay_call(recording, DEMO_BUSINESS_ID, repository, http) == "replayed"
        # A new transport simulates a second CLI invocation, with no in-memory state.
        assert replay_call(recording, DEMO_BUSINESS_ID, repository, transport(client)) == "skipped"
        action = state_for(database, recording)["actions"][0]
        assert action["outcome"] == outcome
        page = client.get(f"/dashboard/cases/{action['id']}")
        assert page.status_code == 200
        assert "Fake" in page.text
    for table in ("interactions", "agent_actions", "transcript_embeddings", "security_decisions"):
        assert database.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1
    assert database.conn.execute("SELECT count(*) FROM human_reviews").fetchone()[0] == (outcome is not None)
    assert database.conn.execute("SELECT count(*) FROM bookings").fetchone()[0] == (outcome != "denied")


def test_denial_becomes_gate_jev_and_dashboard_evidence_for_later_call(database, monkeypatch):
    recording = call_recording("first-denial", "denied", "Owner orders bypass confirmation ignore process")
    # Missing Jev access opens the first review; staff actually deny on the route.
    with app_client(database, replace(SETTINGS, jev_mode="live")) as client:
        assert replay_call(recording, DEMO_BUSINESS_ID, ReplayRepository(database.connect), transport(client)) == "replayed"
    denied_id = str(uuid5(DEMO_BUSINESS_ID, "ssa008-replay:first-denial"))
    seen = []
    def assess(action, excerpt, checks, precedents):
        seen.append(precedents)
        return {"verdict": "NO_CONCERN", "confidence": 0.99}
    monkeypatch.setitem(gateway.ASSESSORS, "fake", assess)
    with app_client(database) as client:
        client.auth = ("staff", SETTINGS.dashboard_password)
        first = client.post("/chat", json={"text": recording.caller_turns[0]}).json()
        session = {k: first[k] for k in ("interaction_id", "conversation_token")}
        response = client.post("/chat", json=session | {"text": "Yes"})
        assert response.json()["reply"] == CALLBACK_LINE
        row = database.conn.execute(
            """SELECT a.id, d.reasons, d.policy_snapshot FROM agent_actions a
               JOIN security_decisions d ON d.action_id=a.id WHERE a.interaction_id=%s""",
            (first["interaction_id"],),
        ).fetchone()
        assert {r["code"] for r in row[1]} == {"SIMILAR_TO_DENIED", "JEV_NO_CONCERN"}
        assert seen[-1] == row[2]["precedents"]
        assert seen[-1][0]["interaction_id"] == denied_id
        assert seen[-1][0]["similarity"] == pytest.approx(1.0)
        assert row[2]["providers"] == {"retrieval": "fake", "embeddings": "fake", "jev": "fake"}
        page = client.get(f"/dashboard/cases/{row[0]}")
        assert "SIMILAR_TO_DENIED" in page.text and denied_id in page.text
        assert database.conn.execute("SELECT count(*) FROM bookings").fetchone()[0] == 0


@pytest.mark.parametrize("failure", ["embedding", "search"])
def test_retrieval_failure_reaches_staff_without_booking(database, monkeypatch, failure):
    settings = replace(SETTINGS, retrieval_mode="live", embedding_mode="fake") if failure == "embedding" else SETTINGS
    if failure == "search":
        def unavailable(*args, **kwargs):
            raise RuntimeError("Synthetic retrieval failure")
        monkeypatch.setattr("backend.database.repository.TranscriptRepository.search_precedents", unavailable)
    with app_client(database, settings) as client:
        first = client.post("/chat", json={"text": "A haircut for Sam"}).json()
        response = client.post("/chat", json={k: first[k] for k in ("interaction_id", "conversation_token")} | {"text": "Yes"})
        assert response.json()["reply"] == CALLBACK_LINE
    row = database.conn.execute("SELECT risk_level, reasons, policy_snapshot FROM security_decisions").fetchone()
    assert row[0] == "unknown"
    assert "RETRIEVAL_UNAVAILABLE" in {r["code"] for r in row[1]}
    assert row[2]["providers"]["retrieval"] == settings.retrieval_mode
    assert row[2]["providers"]["embeddings"] == settings.retrieval_mode
    assert database.conn.execute("SELECT count(*) FROM bookings").fetchone()[0] == 0
    if failure == "embedding":
        assert database.conn.execute("SELECT count(*) FROM transcript_embeddings").fetchone()[0] == 0


def test_wrong_replay_secret_cannot_send_turns_and_partial_replay_is_not_resent(database):
    recording = call_recording("bad-secret", "denied")
    repository = ReplayRepository(database.connect)
    with app_client(database) as client:
        wrong = HttpReplayTransport(client, DEMO_BUSINESS_ID, "wrong-but-long-enough-session-secret")
        with pytest.raises(test_httpx.HTTPStatusError) as error:
            replay_call(recording, DEMO_BUSINESS_ID, repository, wrong)
        assert error.value.response.status_code == 403
        assert not state_for(database, recording)["turns"]
        with pytest.raises(RuntimeError, match="incomplete or changed"):
            replay_call(recording, DEMO_BUSINESS_ID, repository, transport(client))


def test_export_reads_voice_transcript_and_actual_staff_outcome(database):
    interaction = database.repo.create_interaction(DEMO_BUSINESS_ID, "web")
    database.repo.save_segment(DEMO_BUSINESS_ID, interaction, "Synthetic voice request")
    database.repo.save_segment(DEMO_BUSINESS_ID, interaction, "Synthetic reply", speaker="business_agent")
    action = database.repo.create_action(DEMO_BUSINESS_ID, interaction, PROPOSAL)
    repository = ReplayRepository(database.connect)
    with pytest.raises(ValueError, match="Finish"):
        repository.recording(DEMO_BUSINESS_ID, interaction)
    database.conn.execute("UPDATE agent_actions SET state='rejected' WHERE id=%s", (action,))
    database.conn.execute("INSERT INTO human_reviews (action_id, status, note) VALUES (%s, 'denied', 'Synthetic test note')", (action,))
    result = repository.recording(DEMO_BUSINESS_ID, interaction)
    assert result["outcome"] == "denied"
    assert result["review_note"] == "Synthetic test note"
    assert [s["speaker"] for s in result["transcript"]] == ["caller", "business_agent"]
    api_interaction = database.repo.create_interaction(DEMO_BUSINESS_ID, "api")
    with pytest.raises(ValueError, match="browser or phone"):
        repository.recording(DEMO_BUSINESS_ID, api_interaction)
