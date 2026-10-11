"""Jev adapter tests with an explicit fake client; no TypeSafe requests."""

from types import SimpleNamespace

import pytest

from backend.config import Settings
from backend.providers import jev
from backend.services.gateway import evaluate
from tests.test_gateway import NOW, FakeStore, proposal

SETTINGS = Settings(retrieval_mode="fake", jev_mode="live", typesafe_api_key="synthetic-key")
PRECEDENT = {"interaction_id": "past-call", "summary": "Claimed the owner approved it",
             "outcome": "denied", "similarity": 0.5}


class FakeClient:
    """Stands in for TypeSafeClient and records what it was sent."""

    def __init__(self, choice="NO_CONCERN", confidence=0.93, error=None):
        self.choice, self.confidence, self.error, self.calls = choice, confidence, error, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def system_one(self, state, questions):
        self.calls.append((state, questions))
        if self.error:
            raise self.error
        answer = SimpleNamespace(type="choice", choice=self.choice, confidence=self.confidence,
                                 probabilities={self.choice: 1.0})
        return SimpleNamespace(model="jev-test", answers={jev.QUESTION_ID: answer})


def assess(client, settings=SETTINGS):
    return jev.assess(proposal(callback_number="+15555550123"), "A haircut tomorrow at three",
                      [{"rule": "notice", "result": "pass"}], [PRECEDENT],
                      settings=settings, client_factory=lambda _: client)


def test_answer_is_parsed_and_only_bounded_fields_are_sent():
    client = FakeClient("CONCERN", 0.88)
    assert assess(client) == {"verdict": "CONCERN", "confidence": 0.88, "question": jev.QUESTION,
                              "model": "jev-test", "probabilities": {"CONCERN": 1.0}}
    (state, questions), = client.calls
    assert state == {
        "caller_said": "A haircut tomorrow at three",
        "proposed_booking": {"service": "haircut", "start_time": "2026-10-11T15:00:00-05:00", "party_size": 1},
        "rule_checks": [{"rule": "notice", "result": "pass"}],
        "similar_past_calls": [{"outcome": "denied", "similarity": 0.5, "summary": "Claimed the owner approved it"}],
    }
    question = questions[jev.QUESTION_ID]
    assert question.instructions == jev.QUESTION
    assert set(question.criteria) == {"NO_CONCERN", "CONCERN", "INSUFFICIENT_CONTEXT"}


@pytest.mark.parametrize("client,error", [
    (FakeClient(error=TimeoutError("secret detail")), "TimeoutError"),
    (FakeClient(choice="MAYBE"), "Unexpected Jev answer"),
    (FakeClient(confidence=None), "Unexpected Jev answer"),
    (FakeClient(confidence=1.5), "Unexpected Jev answer"),
])
def test_failure_or_unexpected_answer_is_explicitly_unavailable(client, error):
    result = assess(client)
    assert result == {"verdict": "UNAVAILABLE", "confidence": None, "question": jev.QUESTION, "error": error}


def test_missing_key_is_unavailable_without_a_request():
    client = FakeClient()
    assert assess(client, Settings(jev_mode="live"))["error"] == "TYPESAFE_API_KEY is not set"
    assert client.calls == []
    assert "synthetic-key" not in repr(SETTINGS)


@pytest.mark.parametrize("client,decision,evidence", [
    (FakeClient(), "allow", {"verdict": "NO_CONCERN", "confidence": 0.93, "question": jev.QUESTION,
                             "mode": "live", "model": "jev-test"}),
    (FakeClient("CONCERN"), "escalate", {"verdict": "CONCERN", "confidence": 0.93, "question": jev.QUESTION,
                                         "mode": "live", "model": "jev-test"}),
    (FakeClient(error=TimeoutError()), "escalate", {"mode": "live", "error": "TimeoutError"}),
])
def test_gate_in_live_mode_uses_the_adapter_and_escalates_when_it_is_unavailable(monkeypatch, client, decision, evidence):
    monkeypatch.setattr(jev, "_client", lambda settings: client)
    result = evaluate("action", now=NOW, store=FakeStore(proposal()), settings=SETTINGS)
    assert (result.decision, result.reasons[-1]["evidence"]) == (decision, evidence)
    assert result.policy_snapshot["providers"]["jev"] == "live"
