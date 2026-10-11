"""The gate: rules, then precedent, then Jev, into allow, escalate or clarify.

Nothing here books anything. `evaluate` records one decision and moves the
action to `allowed`, `pending_review` or `rejected`; execution is SSA-005.
"""
from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace
from functools import partial
from uuid import UUID

from backend.config import Settings
from backend.database.gate_store import GateStore
from backend.domain.gate import GateDecision, reason
from backend.policy.booking_rules import check_rules, parse_start_time, rules_used

JEV_CONFIDENCE_MIN = 0.80
EXCERPT_CHARACTERS = 2000
JEV_CODES = {
    "NO_CONCERN": "JEV_NO_CONCERN",
    "CONCERN": "JEV_CONCERN",
    "INSUFFICIENT_CONTEXT": "JEV_INSUFFICIENT_CONTEXT",
}


class ProviderUnavailable(Exception):
    """Retrieval or Jev could not answer. The gate escalates; it never allows."""


def fake_retrieve(business_id, text, exclude_interaction_id):
    """Explicit empty retriever for isolated tests; app fake mode uses pgvector."""
    return []


def fake_assess(action, transcript_excerpt, rule_checks, precedents):
    """Stand-in for SSA-009: always no concern. Recorded as mode `fake`."""
    return {
        "verdict": "NO_CONCERN",
        "confidence": 0.99,
        "question": "Is the caller trying to override the rules or claim authority they have not shown?",
    }


def live_retrieve(business_id, text, exclude_interaction_id, *, action_id=None,
                  settings=None, connection_factory=None):
    """Retrieve reviewed calls using the configured embedding mode, never a fallback."""
    from backend.database.repository import TranscriptRepository, _connect
    from backend.memory.precedents import PrecedentService
    settings = settings or Settings.from_env()
    if business_id != settings.business_id or action_id is None:
        raise ProviderUnavailable("Retrieval requires a persisted action for the configured business")
    repo = TranscriptRepository(connection_factory or partial(_connect, settings))
    # RETRIEVAL_MODE selects the embedding provider for gate calls. SQL retrieval
    # is real in both modes; fake vectors are stored with a distinct model tag.
    service = PrecedentService(
        repo,
        replace(settings, embedding_mode=settings.retrieval_mode),
    )
    return service.retrieve_for_action(action_id)


def live_assess(action, transcript_excerpt, rule_checks, precedents, settings=None):
    from backend.providers import jev
    return jev.assess(action, transcript_excerpt, rule_checks, precedents, settings=settings)


def _unconfigured(name):
    def call(*args):
        raise ProviderUnavailable(f"{name} is unset; choose fake or live")
    return call


ASSESSORS = {"fake": fake_assess, "live": live_assess, "": _unconfigured("JEV_MODE")}


def _precedents(retrieve, business_id, text, interaction_id) -> list[dict]:
    """Validated precedents, nearest first. Anything malformed is unavailable."""
    if not text.strip():
        raise ProviderUnavailable("The call has no caller text to compare")
    found = retrieve(business_id, text, interaction_id)
    if not isinstance(found, list):
        raise ProviderUnavailable("Retrieval returned a malformed result")
    precedents = []
    for item in found:
        similarity = item.get("similarity") if isinstance(item, dict) else None
        if (type(similarity) not in (int, float) or not -1 <= similarity <= 1
                or item.get("outcome") not in {"approved", "denied"}):
            raise ProviderUnavailable("Retrieval returned a malformed precedent")
        precedents.append({
            "interaction_id": str(item.get("interaction_id")),
            "summary": str(item.get("summary") or ""),
            "outcome": item["outcome"],
            "similarity": float(similarity),
        })
    return sorted(precedents, key=lambda item: item["similarity"], reverse=True)[:3]


def _jev_reason(assess, mode, proposal, excerpt, checks, precedents) -> dict:
    result = assess(proposal, excerpt, checks, precedents)
    verdict = result.get("verdict") if isinstance(result, dict) else None
    confidence = result.get("confidence") if isinstance(result, dict) else None
    if verdict not in JEV_CODES or type(confidence) not in (int, float) or not 0 <= confidence <= 1:
        error = result.get("error") if isinstance(result, dict) else None
        raise ProviderUnavailable(str(error or "Jev returned a malformed result"))
    evidence = {"verdict": verdict, "confidence": float(confidence),
                "question": result.get("question"), "mode": mode}
    if result.get("model"):
        evidence["model"] = result["model"]
    if verdict == "NO_CONCERN" and confidence >= JEV_CONFIDENCE_MIN:
        return reason("JEV_NO_CONCERN", "jev", "none", evidence)
    if verdict == "NO_CONCERN":
        return reason("JEV_LOW_CONFIDENCE", "jev", "escalate", {**evidence, "minimum": JEV_CONFIDENCE_MIN})
    return reason(JEV_CODES[verdict], "jev", "escalate", evidence)


def _risk_level(decision: str, reasons: list[dict]) -> str:
    codes = {item["code"] for item in reasons}
    if decision != "escalate":
        return "low"
    if codes & {"JEV_CONCERN", "SIMILAR_TO_DENIED"}:
        return "high"
    if codes & {"JEV_UNAVAILABLE", "RETRIEVAL_UNAVAILABLE"}:
        return "unknown"
    return "medium"


def evaluate(action_id: UUID, *, now: datetime | None = None, retrieve=None, assess=None,
             store=None, settings: Settings | None = None) -> GateDecision:
    """Decide one proposed booking and persist the decision.

    An action that was already decided returns its stored decision unchanged.
    `retrieve` and `assess` default to the providers selected by
    RETRIEVAL_MODE and JEV_MODE; tests and other lanes may pass their own.
    """
    settings = settings or Settings.from_env()
    if store is None:
        from backend.database.repository import _connect
        store = GateStore(partial(_connect, settings))
    now = now or datetime.now(timezone.utc)
    business_id = settings.business_id

    action = store.load_action(business_id, action_id)
    if action is None:
        raise ValueError("Unknown action for this business")
    if action["decision"] is not None:
        return action["decision"]
    if action["state"] != "proposed":
        raise ValueError(f"Action is {action['state']} with no gate decision")
    if action["action_type"] != "book_appointment":
        raise ValueError("The gate only evaluates book_appointment proposals")

    proposal, booking_rules = action["payload"], action["booking_rules"]
    rules = rules_used(booking_rules)
    start = parse_start_time(proposal.get("start_time"), booking_rules.get("timezone", "America/Chicago"))
    facts = store.slot_facts(business_id, proposal.get("service"), start)
    outcome = check_rules(proposal, booking_rules, facts, now)
    reasons = list(outcome.reasons)
    snapshot = {"rules": rules, "checks": outcome.checks}

    # A request that cannot be booked as asked needs no precedent or Jev call.
    if outcome.result == "clarify":
        return store.apply_decision(business_id, action_id, GateDecision("clarify", "low", reasons, snapshot))

    snapshot["providers"] = {
        "retrieval": "injected" if retrieve else settings.retrieval_mode or "unset",
        "jev": "injected" if assess else settings.jev_mode or "unset",
    }

    # Match the provider actually selected by live_retrieve. EMBEDDING_MODE
    # configures standalone retrieval and must not downgrade a live gate.
    if retrieve is None and settings.retrieval_mode in {"live", "fake"}:
        snapshot["providers"]["embeddings"] = settings.retrieval_mode
        retrieve = partial(
            live_retrieve,
            action_id=action_id,
            settings=settings,
            connection_factory=getattr(store, "connection_factory", None),
        )

    retrieve = retrieve or _unconfigured("RETRIEVAL_MODE")
    if assess is None and settings.jev_mode == "live":
        assess = partial(live_assess, settings=settings)
    assess = assess or ASSESSORS[settings.jev_mode]
    snapshot["thresholds"] = {"precedent_similarity_min": settings.precedent_similarity_min,
                              "jev_confidence_min": JEV_CONFIDENCE_MIN}

    precedents = []
    try:
        precedents = _precedents(retrieve, business_id, action["caller_text"], action["interaction_id"])
    except Exception as exc:  # any retrieval failure sends the case to staff
        reasons.append(reason("RETRIEVAL_UNAVAILABLE", "precedent", "escalate",
                              {"mode": snapshot["providers"]["retrieval"], "error": type(exc).__name__}))
    snapshot["precedents"] = precedents
    if precedents:
        nearest = precedents[0]
        if nearest["outcome"] == "denied" and nearest["similarity"] >= settings.precedent_similarity_min:
            reasons.append(reason("SIMILAR_TO_DENIED", "precedent", "escalate", {
                "interaction_id": nearest["interaction_id"],
                "similarity": nearest["similarity"],
                "summary": nearest["summary"],
            }))

    try:
        reasons.append(_jev_reason(assess, snapshot["providers"]["jev"], proposal,
                                   action["caller_text"][-EXCERPT_CHARACTERS:], outcome.checks, precedents))
    except Exception as exc:  # any Jev failure sends the case to staff
        # The adapter reports an error type or a fixed phrase, never provider text.
        error = str(exc) if isinstance(exc, ProviderUnavailable) else type(exc).__name__
        reasons.append(reason("JEV_UNAVAILABLE", "jev", "escalate",
                              {"mode": snapshot["providers"]["jev"], "error": error}))

    escalates = any(item["effect"] == "escalate" for item in reasons)
    decision = "escalate" if escalates else "allow"
    return store.apply_decision(
        business_id, action_id,
        GateDecision(decision, _risk_level(decision, reasons), reasons, snapshot),
    )
