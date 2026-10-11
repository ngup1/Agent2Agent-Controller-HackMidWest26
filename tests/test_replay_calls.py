"""Synthetic test inputs only; these are not runtime recorded-call fixtures."""
import json
from unittest.mock import Mock

import httpx
import pytest

from backend.config import DEMO_BUSINESS_ID
from backend.scripts.check_embeddings import check_separation
from backend.scripts.replay_calls import (
    HttpReplayTransport, RecordedCall, load_recordings, replay_call,
)


def recording(outcome="denied"):
    return RecordedCall("unit-test", "manipulation", ("Test request",), outcome,
                        ({"speaker": "caller", "content": "Test request"},))


def test_recordings_must_exist_and_come_from_team_recordings(tmp_path):
    with pytest.raises(ValueError, match="No team recordings"):
        load_recordings(tmp_path)
    path = tmp_path / "test.json"
    path.write_text(json.dumps({"source": "handwritten"}))
    with pytest.raises(ValueError, match="team-recording"):
        load_recordings(tmp_path)
    path.write_text(json.dumps({"source": "team-recording", "id": "test", "category": "routine",
                                "outcome": None, "transcript": [
                                    {"speaker": "caller", "content": "A test"},
                                    {"speaker": "business_agent", "content": "Test reply"},
                                    {"speaker": "caller", "content": "Yes"}]}))
    assert load_recordings(tmp_path)[0].caller_turns == ("A test", "Yes")


def test_partial_replay_is_not_resent():
    repo, transport = Mock(), Mock()
    repo.state.return_value = {"reference": recording().reference, "turns": [], "actions": []}
    with pytest.raises(RuntimeError, match="incomplete or changed"):
        replay_call(recording(), DEMO_BUSINESS_ID, repo, transport)
    transport.turn.assert_not_called()
    transport.decide.assert_not_called()


def test_auto_allowed_call_cannot_fabricate_saved_denial():
    call = recording()
    repo, transport = Mock(), Mock()
    repo.state.side_effect = [None, {"reference": call.reference, "turns": ["Test request"],
                                    "actions": [{"id": "a", "state": "executed", "outcome": None, "review_id": None}]}]
    with pytest.raises(RuntimeError, match="will not fabricate"):
        replay_call(call, DEMO_BUSINESS_ID, repo, transport)
    transport.decide.assert_not_called()


def test_transport_posts_turn_and_review_to_application():
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200)
    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(handle)) as client:
        transport = HttpReplayTransport(client, "/reviews/{review_id}/{decision}")
        transport.turn("call", "Synthetic text")
        transport.decide({"id": "action", "review_id": "review"}, "denied")
    assert requests[0].url.path == "/chat"
    assert json.loads(requests[0].content) == {"interaction_id": "call", "text": "Synthetic text"}
    assert requests[1].url.path == "/reviews/review/deny"


def test_calibration_flags_routine_denied_overlap():
    routine = RecordedCall("routine", "routine", ("Synthetic",), None, ())
    denied = recording()
    assert check_separation([routine, denied], {"routine": [1, 0], denied.id: [0, 1]}, 0.8)["routine_below_denied_threshold"]
    assert not check_separation([routine, denied], {"routine": [1, 0], denied.id: [1, 0]}, 0.8)["routine_below_denied_threshold"]
