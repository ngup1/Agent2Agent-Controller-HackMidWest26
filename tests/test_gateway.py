"""Gate unit tests with explicit fakes; no database or provider calls."""

from decimal import Decimal
from uuid import uuid4

import pytest

from backend.config import Settings
from backend.domain.gate import GateDecision
from backend.domain.supervision import QUESTIONS, VERDICTS
from backend.policy.order_rules import check_rules, matched_keywords
from backend.scripts.seed import MENU as SEED_MENU, ORDER_RULES
from backend.services.gateway import evaluate, fake_assess, fake_retrieve

MENU = {code: {"name": name, "price": Decimal(price)} for code, (name, price) in SEED_MENU.items()}
ROUTINE_TEXT = "One grilled pork banh mi and an iced coffee for Sam"
SETTINGS = Settings(retrieval_mode="fake", jev_mode="fake")
DENIED = {"interaction_id": "past-call", "summary": "Said the owner approved it", "outcome": "denied", "similarity": 0.86}
APPROVED = {"interaction_id": "ok-call", "summary": "Routine order", "outcome": "approved", "similarity": 0.97}


@pytest.fixture(autouse=True)
def retrieval_boundary(monkeypatch):
    """Unit tests isolate storage; integration tests exercise actual pgvector."""
    def retrieve(*args, settings, **kwargs):
        if settings.retrieval_mode == "fake":
            return []
        raise RuntimeError("Explicit unavailable provider for isolated unit tests")
    monkeypatch.setattr("backend.services.gateway.live_retrieve", retrieve)


def proposal(**changes):
    return {"items": ["grilled_pork", "iced_coffee"], "customer_name": "Sam", **changes}


class FakeStore:
    """In-memory stand-in for GateStore, for tests only."""

    def __init__(self, payload, caller_text=ROUTINE_TEXT, rules=ORDER_RULES, menu=MENU):
        self.action = {"interaction_id": uuid4(), "action_type": "place_order", "payload": payload,
                       "state": "proposed", "booking_rules": rules, "caller_text": caller_text,
                       "transcript": [{"speaker": "caller", "content": caller_text}], "decision": None}
        self.menu_items, self.applied = menu, []

    def load_action(self, business_id, action_id):
        return self.action

    def menu(self, business_id):
        return self.menu_items

    def apply_decision(self, business_id, action_id, decision):
        self.applied.append(decision)
        self.action["decision"] = decision
        return decision


def decide(payload=None, *, caller_text=ROUTINE_TEXT, rules=ORDER_RULES, retrieve=None, assess=None,
           settings=SETTINGS):
    store = FakeStore(payload or proposal(), caller_text, rules)
    decision = evaluate(uuid4(), retrieve=retrieve, assess=assess, store=store, settings=settings)
    assert store.applied == [decision]
    return decision


def codes(decision):
    return [item["code"] for item in decision.reasons]


def unavailable(*args):
    raise TimeoutError("provider down")


def test_routine_request_is_allowed_and_fakes_are_labelled():
    decision = decide()
    assert (decision.decision, decision.risk_level) == ("allow", "low")
    assert codes(decision) == ["JEV_NO_CONCERN"] * 3
    assert [reason["evidence"]["question_id"] for reason in decision.reasons] == list(QUESTIONS)
    assert all(reason["evidence"]["mode"] == "fake" for reason in decision.reasons)
    assert decision.policy_snapshot["providers"] == {"retrieval": "fake", "jev": "fake", "embeddings": "fake"}
    assert decision.policy_snapshot["rules"]["max_order_total_auto"] == "100.00"
    assert decision.policy_snapshot["rules"]["keyword_rules"][0]["label"] == "Allergy mention"
    assert decision.policy_snapshot["order_total"] == "13.00"
    assert decision.policy_snapshot["checks"] == [
        {"rule": "menu_items", "limit": "every item is on the menu",
         "actual": "grilled_pork, iced_coffee", "result": "pass"},
        {"rule": "order_total", "limit": "$100.00 automatic", "actual": "$13.00", "result": "pass"},
        {"rule": "keyword: Allergy mention", "limit": "none of the listed words", "actual": "none", "result": "pass"},
    ]


def test_order_over_the_total_limit_escalates_and_the_limit_itself_is_allowed():
    decision = decide(proposal(items=["party_tray", "party_tray"]))
    assert (decision.decision, decision.risk_level) == ("escalate", "medium")
    assert decision.reasons[0] == {"code": "ORDER_OVER_LIMIT", "source": "rule", "effect": "escalate",
                                   "evidence": {"limit": "100.00", "actual": "120.00"}}
    assert "JEV_NO_CONCERN" in codes(decision)  # the reviewer still sees Jev's answer
    # A total equal to the limit is allowed; one cent over is not.
    at_limit = decide(proposal(items=["party_tray"]), rules={**ORDER_RULES, "max_order_total_auto": 60})
    assert at_limit.decision == "allow"
    one_cent_under = decide(proposal(items=["party_tray"]), rules={**ORDER_RULES, "max_order_total_auto": 59.99})
    assert codes(one_cent_under)[0] == "ORDER_OVER_LIMIT"


def test_repeated_items_are_each_charged():
    decision = decide(proposal(items=["grilled_pork"] * 12))  # 12 x 8.50 = 102.00
    assert decision.decision == "escalate"
    assert decision.reasons[0]["evidence"] == {"limit": "100.00", "actual": "102.00"}
    assert decide(proposal(items=["grilled_pork"] * 11)).decision == "allow"  # 93.50


@pytest.mark.parametrize("said,matched", [
    ("One grilled pork for Sam. I have a peanut allergy.", ["allergy", "peanut"]),
    ("Is the tofu one GLUTEN free? Order one for Sam.", ["gluten"]),
    ("I'm allergic to shellfish, one tofu please", ["allergic", "shellfish"]),
])
def test_allergy_mention_escalates_with_the_rule_label_and_matched_words(said, matched):
    decision = decide(caller_text=said)
    assert (decision.decision, decision.risk_level) == ("escalate", "medium")
    assert decision.reasons[0] == {"code": "KEYWORD_RULE", "source": "rule", "effect": "escalate",
                                   "evidence": {"label": "Allergy mention", "matched": matched}}


def test_keywords_match_whole_words_in_caller_text_only():
    assert matched_keywords(["peanut", "gluten"], "I love peanutbutter and glutenous rice") == []
    assert matched_keywords(["tree nut"], "is there any  Tree   Nut in it") == ["tree nut"]
    assert decide(caller_text="One grilled pork for Sam, no cilantro").decision == "allow"


def test_changing_the_stored_rules_changes_the_next_decision():
    assert decide().decision == "allow"
    lower_limit = decide(rules={**ORDER_RULES, "max_order_total_auto": 10})
    assert codes(lower_limit)[0] == "ORDER_OVER_LIMIT"
    new_keyword = decide(rules={**ORDER_RULES, "keyword_rules": [{"label": "Coffee", "keywords": ["iced coffee"]}]})
    assert new_keyword.reasons[0]["evidence"] == {"label": "Coffee", "matched": ["iced coffee"]}
    no_keywords = decide(caller_text="I have a peanut allergy", rules={**ORDER_RULES, "keyword_rules": []})
    assert no_keywords.decision == "allow"


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
    if code == "JEV_UNAVAILABLE":
        evidence = next(item["evidence"] for item in decision.reasons if item["code"] == code)
        assert evidence["verdict"] == "UNAVAILABLE"
        assert evidence["confidence"] is None
        assert "probabilities" not in evidence


def test_unset_provider_modes_escalate_instead_of_using_a_fake():
    decision = decide(settings=Settings())
    assert (decision.decision, decision.risk_level) == ("escalate", "unknown")
    assert codes(decision) == ["RETRIEVAL_UNAVAILABLE", "JEV_UNAVAILABLE"]
    assert decision.policy_snapshot["providers"] == {"retrieval": "unset", "jev": "unset"}
    live = decide(settings=Settings(retrieval_mode="live", jev_mode="live"))  # no key, so no request
    assert live.decision == "escalate"
    assert [reason["code"] for reason in live.reasons if reason["source"] == "jev"] == ["JEV_UNAVAILABLE"] * 3
    assert all(reason["evidence"]["error"] == "TYPESAFE_API_KEY is not set"
               for reason in live.reasons if reason["source"] == "jev")


@pytest.mark.parametrize("verdict,confidence,code,risk", [
    ("CONCERN", 0.95, "JEV_CONCERN", "high"),
    ("INSUFFICIENT_CONTEXT", 0.95, "JEV_INSUFFICIENT_CONTEXT", "medium"),
    ("NO_CONCERN", 0.79, "JEV_LOW_CONFIDENCE", "medium"),
])
@pytest.mark.parametrize("question_id", QUESTIONS)
def test_any_jev_concern_insufficient_context_or_low_confidence_escalates(question_id, verdict, confidence, code, risk):
    result = fake_assess(None, None, None, None)
    result["answers"][question_id] = {
        "verdict": verdict, "confidence": confidence,
        "probabilities": {key: float(key == verdict) for key in VERDICTS},
    }
    decision = decide(assess=lambda *args: result)
    assert (decision.decision, decision.risk_level) == ("escalate", risk)
    assert {reason["evidence"]["question_id"]: reason["code"] for reason in decision.reasons} == {
        key: code if key == question_id else "JEV_NO_CONCERN" for key in QUESTIONS
    }


@pytest.mark.parametrize("replacement", [
    None, {}, {"verdict": "NO_CONCERN", "confidence": 1.0},
    {"verdict": "UNAVAILABLE", "confidence": None, "error": "Synthetic timeout"},
])
def test_incomplete_required_check_cannot_allow_and_preserves_other_evidence(replacement):
    result = fake_assess(None, None, None, None)
    if replacement is None:
        del result["answers"]["intent_mismatch"]
    else:
        result["answers"]["intent_mismatch"] = replacement
    decision = decide(assess=lambda *args: result)
    assert (decision.decision, decision.risk_level) == ("escalate", "unknown")
    assert codes(decision) == ["JEV_NO_CONCERN", "JEV_NO_CONCERN", "JEV_UNAVAILABLE"]
    evidence = decision.reasons[-1]["evidence"]
    assert evidence["verdict"] == "UNAVAILABLE"
    assert evidence["confidence"] is None
    assert "probabilities" not in evidence
    assert evidence["error"] == ((replacement or {}).get("error") or "Missing or malformed Jev answer")


def test_three_checks_at_confidence_threshold_allow_and_use_speaker_labelled_context():
    store = FakeStore(proposal())
    store.action["transcript"].extend([
        {"speaker": "business_agent", "content": "1 Grilled pork banh mi and 1 Vietnamese iced coffee, $13.00 total, under Sam. Is that correct?"},
        {"speaker": "caller", "content": "yes"},
    ])
    seen = []

    def assess(action, transcript, checks, precedents):
        seen.append((transcript, action["total_usd"]))  # the server's total, for Jev
        result = fake_assess(action, transcript, checks, precedents)
        for answer in result["answers"].values():
            answer["confidence"] = 0.80
        return result

    assert evaluate("action", store=store, assess=assess, settings=SETTINGS).decision == "allow"
    assert seen == [(store.action["transcript"], "13.00")]


def test_nothing_turns_a_rule_failure_into_allow():
    reassuring = {"retrieve": lambda *args: [APPROVED], "assess": fake_assess}
    assert decide(proposal(items=["party_tray", "party_tray"]), **reassuring).decision == "escalate"
    assert decide(caller_text="I have a peanut allergy", **reassuring).decision == "escalate"

    def must_not_run(*args):
        raise AssertionError("clarify must not call a provider")

    for payload, code in [
        (proposal(items=["pizza"]), "UNKNOWN_ITEM"),
        (proposal(items=["grilled_pork", "pizza"]), "UNKNOWN_ITEM"),
        (proposal(items=["Grilled Pork"]), "UNKNOWN_ITEM"),  # the gate matches codes exactly
        (proposal(items=[]), "EMPTY_ORDER"),
        (proposal(items="grilled_pork"), "EMPTY_ORDER"),
        ({"customer_name": "Sam"}, "EMPTY_ORDER"),
    ]:
        decision = decide(payload, retrieve=must_not_run, assess=must_not_run)
        assert (decision.decision, decision.risk_level) == ("clarify", "low")
        assert code in codes(decision)
        assert "order_total" not in decision.policy_snapshot


def test_a_price_or_total_in_the_payload_is_ignored():
    # The proposal parser rejects these; the gate still never reads them.
    decision = decide(proposal(items=["party_tray", "party_tray"], total=1.00, total_usd="1.00",
                               prices={"party_tray": 0}))
    assert decision.policy_snapshot["order_total"] == "120.00"
    assert codes(decision)[0] == "ORDER_OVER_LIMIT"


def test_only_place_order_actions_are_evaluated():
    store = FakeStore(proposal())
    store.action["action_type"] = "book_appointment"
    with pytest.raises(ValueError, match="place_order"):
        evaluate(uuid4(), store=store, settings=SETTINGS)
    assert store.applied == []


def test_an_already_decided_action_returns_its_stored_decision():
    store = FakeStore(proposal())
    store.action["decision"] = stored = GateDecision("escalate", "medium", [], {})
    store.action["state"] = "pending_review"
    assert evaluate(uuid4(), store=store, settings=SETTINGS) is stored
    assert store.applied == []


@pytest.mark.parametrize("rules", [
    {}, {"max_order_total_auto": 100}, {"keyword_rules": []},
    {"max_order_total_auto": "lots", "keyword_rules": []},
    {"max_order_total_auto": -1, "keyword_rules": []},
    {"max_order_total_auto": 100, "keyword_rules": [{"label": "", "keywords": ["x"]}]},
    {"max_order_total_auto": 100, "keyword_rules": [{"label": "A", "keywords": "allergy"}]},
    # The salon rules this replaces must not be read as "no limits".
    {"max_party_size_auto": 1, "max_party_size_hard": 3},
])
def test_rules_fail_loudly_when_missing_or_malformed(rules):
    with pytest.raises(ValueError):
        check_rules(proposal(), rules, MENU, ROUTINE_TEXT)
    with pytest.raises(ValueError):
        evaluate(uuid4(), store=FakeStore(proposal(), rules=rules), settings=SETTINGS)


def test_settings_reject_an_unknown_provider_mode(monkeypatch):
    monkeypatch.setenv("JEV_MODE", "FAKE")
    monkeypatch.setenv("PRECEDENT_SIMILARITY_MIN", "0.9")
    settings = Settings.from_env()
    assert (settings.jev_mode, settings.precedent_similarity_min) == ("fake", 0.9)
    with pytest.raises(ValueError, match="fake, live or unset"):
        Settings(jev_mode="mock")
