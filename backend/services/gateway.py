"""The gate: rules, then precedent, then Jev, into allow, escalate or clarify.

Nothing here places an order. `evaluate` records one decision and moves the
action to `allowed`, `pending_review` or `rejected`; execution is SSA-005.
"""
from __future__ import annotations

from dataclasses import replace
from functools import partial
from uuid import UUID

from backend.config import Settings
from backend.database.gate_store import GateStore
from backend.domain.gate import GateDecision, reason
from backend.domain.supervision import QUESTIONS, VERDICTS, valid_answer
from backend.policy.order_rules import check_rules, rules_used

JEV_CONFIDENCE_MIN = 0.80
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
    """Explicit stand-in for all three SSA-009 checks, never semantic inference."""
    return {"answers": {key: {
        "verdict": "NO_CONCERN", "confidence": 1.0,
        "probabilities": {verdict: float(verdict == "NO_CONCERN") for verdict in VERDICTS},
    } for key in QUESTIONS}}


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


def _jev_reasons(assess, mode, proposal, excerpt, checks, precedents) -> list[dict]:
    result = assess(proposal, excerpt, checks, precedents)
    answers = result.get("answers") if isinstance(result, dict) else None
    if not isinstance(answers, dict):
        raise ProviderUnavailable("Jev returned a malformed result")
    reasons = []
    for key, question in QUESTIONS.items():
        answer = answers.get(key)
        evidence = {"question_id": key, "label": question["label"],
                    "question": question["question"], "mode": mode}
        if isinstance(result.get("model"), str):
            evidence["model"] = result["model"]
        if not valid_answer(answer):
            error = answer.get("error") if isinstance(answer, dict) and answer.get("verdict") == "UNAVAILABLE" else None
            reasons.append(reason("JEV_UNAVAILABLE", "jev", "escalate", {
                **evidence, "verdict": "UNAVAILABLE", "confidence": None,
                "error": error or "Missing or malformed Jev answer",
            }))
            continue
        evidence.update({name: answer[name] for name in ("verdict", "confidence", "probabilities")})
        reasons.append(_jev_answer_reason(evidence))
    return reasons


def _jev_answer_reason(evidence: dict) -> dict:
    verdict, confidence = evidence["verdict"], evidence["confidence"]
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


def evaluate(action_id: UUID, *, retrieve=None, assess=None,
             store=None, settings: Settings | None = None) -> GateDecision:
    """Decide one proposed order and persist the decision.

    An action that was already decided returns its stored decision unchanged.
    `retrieve` and `assess` default to the providers selected by
    RETRIEVAL_MODE and JEV_MODE; tests and other lanes may pass their own.
    """
    settings = settings or Settings.from_env()
    if store is None:
        from backend.database.repository import _connect
        store = GateStore(partial(_connect, settings))
    business_id = settings.business_id

    action = store.load_action(business_id, action_id)
    if action is None:
        raise ValueError("Unknown action for this business")
    if action["decision"] is not None:
        return action["decision"]
    if action["state"] != "proposed":
        raise ValueError(f"Action is {action['state']} with no gate decision")
    if action["action_type"] != "place_order":
        raise ValueError("The gate only evaluates place_order proposals")

    # The stored rules record keeps the inherited column name, booking_rules.
    proposal, order_rules = action["payload"], action["booking_rules"]
    rules = rules_used(order_rules)
    outcome = check_rules(proposal, order_rules, store.menu(business_id), action["caller_text"])
    reasons = list(outcome.reasons)
    snapshot = {"rules": rules, "checks": outcome.checks}
    if outcome.total is not None:
        snapshot["order_total"] = f"{outcome.total:.2f}"

    # A request that cannot be placed as asked needs no precedent or Jev call.
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
        # Jev sees the server-computed total, never a figure from the model.
        reasons.extend(_jev_reasons(assess, snapshot["providers"]["jev"],
                                    {**proposal, "total_usd": snapshot.get("order_total")},
                                    action["transcript"], outcome.checks, precedents))
    except Exception as exc:  # any Jev failure sends the case to staff
        # The adapter reports an error type or a fixed phrase, never provider text.
        error = str(exc) if isinstance(exc, ProviderUnavailable) else type(exc).__name__
        reasons.append(reason("JEV_UNAVAILABLE", "jev", "escalate",
                              {"mode": snapshot["providers"]["jev"], "verdict": "UNAVAILABLE",
                               "confidence": None, "error": error}))

    escalates = any(item["effect"] == "escalate" for item in reasons)
    decision = "escalate" if escalates else "allow"
    return store.apply_decision(
        business_id, action_id,
        GateDecision(decision, _risk_level(decision, reasons), reasons, snapshot),
    )
