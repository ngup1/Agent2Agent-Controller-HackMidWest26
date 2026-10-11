"""Gate unit tests with explicit fakes; no database or provider calls."""

from datetime import datetime
from uuid import uuid4

import pytest

from backend.config import Settings
from backend.domain.gate import GateDecision
from backend.policy.booking_rules import SlotFacts, check_rules
from backend.scripts.seed import BOOKING_RULES
from backend.services.gateway import evaluate, fake_assess, fake_retrieve

NOW = datetime.fromisoformat("2026-10-10T08:00:00-05:00")
OPEN_SLOT = SlotFacts(service_known=True, slot_found=True, closed=False, capacity=3, booked=0)
SETTINGS = Settings(retrieval_mode="fake", jev_mode="fake")
DENIED = {"interaction_id": "past-call", "summary": "Said the owner approved it", "outcome": "denied", "similarity": 0.86}
APPROVED = {"interaction_id": "ok-call", "summary": "Routine haircut", "outcome": "approved", "similarity": 0.97}


@pytest.fixture(autouse=True)
def retrieval_boundary(monkeypatch):
    """Unit tests isolate storage; integration tests exercise actual pgvector."""
    def retrieve(*args, settings, **kwargs):
        if settings.retrieval_mode == "fake":
            return []
        raise RuntimeError("Explicit unavailable provider for isolated unit tests")
    monkeypatch.setattr("backend.services.gateway.live_retrieve", retrieve)


def proposal(**changes):
    return {"service": "haircut", "start_time": "2026-10-11T15:00:00-05:00", "party_size": 1,
            "customer_name": "Sam", **changes}


class FakeStore:
    """In-memory stand-in for GateStore, for tests only."""

    def __init__(self, payload, facts=OPEN_SLOT, caller_text="I'd like a haircut tomorrow at three"):
        self.action = {"interaction_id": uuid4(), "action_type": "book_appointment", "payload": payload,
                       "state": "proposed", "booking_rules": BOOKING_RULES, "caller_text": caller_text,
                       "decision": None}
        self.facts, self.applied = facts, []

    def load_action(self, business_id, action_id):
        return self.action

    def slot_facts(self, business_id, service_code, start):
        return self.facts

    def apply_decision(self, business_id, action_id, decision):
        self.applied.append(decision)
        self.action["decision"] = decision
        return decision


def decide(payload=None, *, facts=OPEN_SLOT, retrieve=None, assess=None, settings=SETTINGS):
    store = FakeStore(payload or proposal(), facts)
    decision = evaluate(uuid4(), now=NOW, retrieve=retrieve, assess=assess, store=store, settings=settings)
    assert store.applied == [decision]
    return decision


def codes(decision):
    return [item["code"] for item in decision.reasons]


def unavailable(*args):
    raise TimeoutError("provider down")


def test_routine_request_is_allowed_and_fakes_are_labelled():
    decision = decide()
    assert (decision.decision, decision.risk_level) == ("allow", "low")
    assert codes(decision) == ["JEV_NO_CONCERN"]
    assert decision.reasons[0]["evidence"]["mode"] == "fake"
    assert decision.policy_snapshot["providers"] == {
        "retrieval": "fake",
        "jev": "fake",
        "embeddings": "fake"
    }
    assert decision.policy_snapshot["rules"] == {
        "max_party_size_auto": 1, "max_party_size_hard": 3,
        "min_notice_minutes_auto": 120, "min_notice_minutes_hard": 30,
    }
    assert {"rule": "notice", "limit": "120 minutes automatic, 30 minimum", "actual": 1860, "result": "pass"} \
        in decision.policy_snapshot["checks"]


def test_group_of_three_escalates_with_group_size():
    decision = decide(proposal(party_size=3))
    assert (decision.decision, decision.risk_level) == ("escalate", "medium")
    assert decision.reasons[0] == {"code": "GROUP_SIZE", "source": "rule", "effect": "escalate",
                                   "evidence": {"limit": 1, "actual": 3}}
    assert "JEV_NO_CONCERN" in codes(decision)  # the reviewer still sees Jev's answer


def test_short_notice_escalates_and_under_the_minimum_is_not_bookable():
    short = decide(proposal(start_time="2026-10-10T09:30:00-05:00"))
    assert (short.decision, codes(short)[0]) == ("escalate", "SHORT_NOTICE")
    too_soon = decide(proposal(start_time="2026-10-10T08:20:00-05:00"))
    assert (too_soon.decision, codes(too_soon)) == ("clarify", ["SLOT_UNAVAILABLE"])
    exactly = decide(proposal(start_time="2026-10-10T10:00:00-05:00"))
    assert exactly.decision == "allow"


def test_denied_precedent_escalates_even_when_jev_is_unavailable():
    decision = decide(retrieve=lambda *args: [APPROVED | {"similarity": 0.5}, DENIED], assess=unavailable)
    assert (decision.decision, decision.risk_level) == ("escalate", "high")
    assert codes(decision) == ["SIMILAR_TO_DENIED", "JEV_UNAVAILABLE"]
    assert decision.reasons[0]["evidence"] == {
        "interaction_id": "past-call", "similarity": 0.86, "summary": "Said the owner approved it"}
    assert [item["outcome"] for item in decision.policy_snapshot["precedents"]] == ["denied", "approved"]


def test_precedent_below_threshold_or_approved_changes_nothing():
    assert decide(retrieve=lambda *args: [DENIED | {"similarity": 0.79}]).decision == "allow"
    assert decide(retrieve=lambda *args: [APPROVED, DENIED | {"similarity": 0.9}]).decision == "allow"


@pytest.mark.parametrize("retrieve,assess,code", [
    (unavailable, fake_assess, "RETRIEVAL_UNAVAILABLE"),
    (fake_retrieve, unavailable, "JEV_UNAVAILABLE"),
    (lambda *args: [{"outcome": "maybe", "similarity": 0.9}], fake_assess, "RETRIEVAL_UNAVAILABLE"),
    (fake_retrieve, lambda *args: {"verdict": "NO_CONCERN"}, "JEV_UNAVAILABLE"),
    (fake_retrieve, lambda *args: {"verdict": "unavailable", "confidence": 1.0}, "JEV_UNAVAILABLE"),
    (fake_retrieve, lambda *args: None, "JEV_UNAVAILABLE"),
])
def test_provider_failure_or_malformed_result_escalates_as_unknown(retrieve, assess, code):
    decision = decide(retrieve=retrieve, assess=assess)
    assert (decision.decision, decision.risk_level) == ("escalate", "unknown")
    assert code in codes(decision)


def test_unset_provider_modes_escalate_instead_of_using_a_fake():
    decision = decide(settings=Settings())
    assert (decision.decision, decision.risk_level) == ("escalate", "unknown")
    assert codes(decision) == ["RETRIEVAL_UNAVAILABLE", "JEV_UNAVAILABLE"]
    assert decision.policy_snapshot["providers"] == {"retrieval": "unset", "jev": "unset"}
    live = decide(settings=Settings(retrieval_mode="live", jev_mode="live"))  # no key, so no request
    assert (live.decision, live.reasons[-1]["evidence"]) == (
        "escalate", {"mode": "live", "error": "TYPESAFE_API_KEY is not set"})


@pytest.mark.parametrize("verdict,confidence,code,risk", [
    ("CONCERN", 0.95, "JEV_CONCERN", "high"),
    ("INSUFFICIENT_CONTEXT", 0.95, "JEV_INSUFFICIENT_CONTEXT", "medium"),
    ("NO_CONCERN", 0.79, "JEV_LOW_CONFIDENCE", "medium"),
])
def test_jev_concern_insufficient_context_or_low_confidence_escalates(verdict, confidence, code, risk):
    decision = decide(assess=lambda *args: {"verdict": verdict, "confidence": confidence, "question": "q"})
    assert (decision.decision, decision.risk_level, codes(decision)) == ("escalate", risk, [code])


def test_nothing_turns_a_rule_failure_into_allow():
    reassuring = {"retrieve": lambda *args: [APPROVED], "assess": fake_assess}
    assert decide(proposal(party_size=2), **reassuring).decision == "escalate"

    def must_not_run(*args):
        raise AssertionError("clarify must not call a provider")

    for payload, facts, code in [
        (proposal(service="massage"), SlotFacts(), "UNKNOWN_SERVICE"),
        (proposal(), SlotFacts(service_known=True), "SLOT_UNAVAILABLE"),
        (proposal(), SlotFacts(True, True, True, 3, 0), "SLOT_UNAVAILABLE"),
        (proposal(start_time="2026-10-09T15:00:00-05:00"), OPEN_SLOT, "SLOT_UNAVAILABLE"),
        (proposal(start_time="tomorrow"), OPEN_SLOT, "SLOT_UNAVAILABLE"),
        (proposal(party_size=4), OPEN_SLOT, "PARTY_TOO_LARGE"),
        (proposal(party_size=0), OPEN_SLOT, "PARTY_TOO_LARGE"),
        (proposal(party_size=2), SlotFacts(True, True, False, 3, 2), "NO_CAPACITY"),
    ]:
        decision = decide(payload, facts=facts, retrieve=must_not_run, assess=must_not_run)
        assert (decision.decision, decision.risk_level) == ("clarify", "low")
        assert code in codes(decision)


def test_an_already_decided_action_returns_its_stored_decision():
    store = FakeStore(proposal())
    store.action["decision"] = stored = GateDecision("escalate", "medium", [], {})
    store.action["state"] = "pending_review"
    assert evaluate(uuid4(), now=NOW, store=store, settings=SETTINGS) is stored
    assert store.applied == []


def test_rules_fail_loudly_when_the_business_is_not_seeded_and_read_local_times():
    with pytest.raises(ValueError, match="run the seed"):
        check_rules(proposal(), {}, OPEN_SLOT, NOW)
    naive = check_rules(proposal(start_time="2026-10-11T15:00:00"), BOOKING_RULES, OPEN_SLOT, NOW)
    assert naive.result == "pass"
    assert {item["rule"]: item["actual"] for item in naive.checks}["notice"] == 1860


def test_settings_reject_an_unknown_provider_mode(monkeypatch):
    monkeypatch.setenv("JEV_MODE", "FAKE")
    monkeypatch.setenv("PRECEDENT_SIMILARITY_MIN", "0.9")
    settings = Settings.from_env()
    assert (settings.jev_mode, settings.precedent_similarity_min) == ("fake", 0.9)
    with pytest.raises(ValueError, match="fake, live or unset"):
        Settings(jev_mode="mock")
