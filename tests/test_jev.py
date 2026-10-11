"""SSA-009 request, parsing and gate checks with a fake SDK; no live inference."""
from types import SimpleNamespace

import pytest

from backend.config import Settings
from backend.domain.supervision import QUESTIONS, VERDICTS
from backend.providers import jev
from backend.services.gateway import evaluate, fake_retrieve
from tests.test_gateway import NOW, FakeStore, proposal

SETTINGS = Settings(retrieval_mode="fake", jev_mode="live", typesafe_api_key="synthetic-key")
PRECEDENT = {"interaction_id": "past-call", "summary": "Claimed the owner approved it",
             "outcome": "denied", "similarity": 0.5}
CONVERSATION = [
    {"speaker": "caller", "content": "A haircut tomorrow at three", "recorded_at": NOW.isoformat()},
    {"speaker": "business_agent", "content": "A haircut at 3 pm for one. Is that correct?", "recorded_at": NOW.isoformat()},
    {"speaker": "caller", "content": "yes", "recorded_at": NOW.isoformat()},
]


def sdk_answer(choice="NO_CONCERN", confidence=0.93, **changes):
    return SimpleNamespace(**{
        "type": "choice", "choice": choice, "confidence": confidence,
        "probabilities": {key: float(key == choice) for key in VERDICTS}, **changes,
    })


class FakeClient:
    """Explicit SDK stand-in, not a semantic model of Jev."""

    def __init__(self, choice="NO_CONCERN", confidence=0.93, error=None, answers=None):
        self.error, self.calls = error, []
        self.answers = answers if answers is not None else {key: sdk_answer(choice, confidence) for key in QUESTIONS}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def system_one(self, state, questions):
        self.calls.append((state, questions))
        if self.error:
            raise self.error
        return SimpleNamespace(model="jev-test", answers=self.answers)


def assess(client, settings=SETTINGS, conversation=CONVERSATION):
    return jev.assess(proposal(callback_number="+15555550123"), conversation,
                      [{"rule": "notice", "result": "pass"}], [PRECEDENT],
                      settings=settings, client_factory=lambda _: client)


def test_three_questions_share_one_request_with_confirmation_and_allowlisted_fields():
    client = FakeClient("CONCERN", 0.88)
    result = assess(client)
    assert result["model"] == "jev-test"
    assert set(result["answers"]) == set(QUESTIONS)
    for answer in result["answers"].values():
        assert answer == {"verdict": "CONCERN", "confidence": 0.88,
                          "probabilities": {"NO_CONCERN": 0.0, "CONCERN": 1.0, "INSUFFICIENT_CONTEXT": 0.0}}
    (state, questions), = client.calls
    assert state == {
        "conversation": CONVERSATION,
        "conversation_truncated": False,
        "proposed_booking": {"service": "haircut", "start_time": "2026-10-11T15:00:00-05:00", "party_size": 1},
        "rule_checks": [{"rule": "notice", "result": "pass"}],
        "similar_past_calls": [{"outcome": "denied", "similarity": 0.5, "summary": "Claimed the owner approved it"}],
    }
    assert set(questions) == {"instruction_override", "authority_manipulation", "intent_mismatch"}
    for key, question in questions.items():
        assert question.instructions == jev.GUIDANCE + QUESTIONS[key]["question"]
        assert question.criteria == QUESTIONS[key]["criteria"]


def test_excerpt_is_bounded_and_keeps_latest_recap_confirmation_and_speaker_labels():
    turns = [{"speaker": "caller", "content": "old " * 3000}] + CONVERSATION
    client = FakeClient()
    assess(client, conversation=turns)
    state = client.calls[0][0]
    assert state["conversation_truncated"] is True
    assert sum(len(turn["content"]) for turn in state["conversation"]) == jev.MAX_TRANSCRIPT_CHARACTERS
    assert state["conversation"][-3:] == CONVERSATION
    client = FakeClient()
    assess(client, conversation=CONVERSATION * 10)
    assert client.calls[0][0]["conversation"] == (CONVERSATION * 10)[-jev.MAX_TRANSCRIPT_TURNS:]
    assert client.calls[0][0]["conversation_truncated"] is True


@pytest.mark.parametrize("changes", [
    {"choice": "MAYBE"}, {"type": "score"}, {"confidence": None}, {"confidence": True},
    {"confidence": float("nan")}, {"confidence": float("inf")}, {"confidence": 1.5},
    {"probabilities": None}, {"probabilities": {"NO_CONCERN": 1.0}},
    {"probabilities": {"NO_CONCERN": 0.5, "CONCERN": 0.0, "INSUFFICIENT_CONTEXT": 0.0}},
    {"probabilities": {"NO_CONCERN": 1.0, "CONCERN": float("nan"), "INSUFFICIENT_CONTEXT": 0.0}},
    {"probabilities": {"NO_CONCERN": 0.1, "CONCERN": 0.9, "INSUFFICIENT_CONTEXT": 0.0}},
])
def test_malformed_answer_is_unavailable_without_discarding_valid_siblings(changes):
    client = FakeClient()
    client.answers["intent_mismatch"] = sdk_answer(**changes)
    result = assess(client)
    assert result["answers"]["intent_mismatch"] == {
        "verdict": "UNAVAILABLE", "confidence": None, "error": "Missing or malformed Jev answer",
    }
    assert result["answers"]["instruction_override"]["verdict"] == "NO_CONCERN"
    assert result["answers"]["authority_manipulation"]["verdict"] == "NO_CONCERN"


def test_missing_answer_and_empty_response_are_unavailable():
    client = FakeClient()
    del client.answers["intent_mismatch"]
    assert assess(client)["answers"]["intent_mismatch"]["verdict"] == "UNAVAILABLE"
    assert all(answer["verdict"] == "UNAVAILABLE" for answer in assess(FakeClient(answers={}))["answers"].values())


def test_timeout_is_unavailable_and_does_not_expose_exception_text():
    result = assess(FakeClient(error=TimeoutError("secret detail")))
    assert result == {"answers": {key: {"verdict": "UNAVAILABLE", "confidence": None, "error": "TimeoutError"}
                                  for key in QUESTIONS}}


def test_missing_key_is_unavailable_without_a_request():
    client = FakeClient()
    result = assess(client, Settings(jev_mode="live"))
    assert all(answer["error"] == "TYPESAFE_API_KEY is not set" for answer in result["answers"].values())
    assert client.calls == []
    assert "synthetic-key" not in repr(SETTINGS)


@pytest.mark.parametrize("client,decision,code", [
    (FakeClient(), "allow", "JEV_NO_CONCERN"),
    (FakeClient("CONCERN"), "escalate", "JEV_CONCERN"),
    (FakeClient(error=TimeoutError()), "escalate", "JEV_UNAVAILABLE"),
])
def test_gate_uses_adapter_and_records_three_results(monkeypatch, client, decision, code):
    monkeypatch.setattr(jev, "_client", lambda settings: client)
    result = evaluate("action", now=NOW, store=FakeStore(proposal()), settings=SETTINGS, retrieve=fake_retrieve)
    assert result.decision == decision
    assert [reason["code"] for reason in result.reasons] == [code] * 3
    assert [reason["evidence"]["question_id"] for reason in result.reasons] == list(QUESTIONS)
    assert result.policy_snapshot["providers"]["jev"] == "live"
    if code != "JEV_UNAVAILABLE":
        assert all(reason["evidence"]["model"] == "jev-test" for reason in result.reasons)
