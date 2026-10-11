"""Presentation helpers; never make or change a gate decision."""
from datetime import datetime
from zoneinfo import ZoneInfo

REASONS = {
    "GROUP_SIZE": "The group is larger than the automatic booking limit.",
    "SHORT_NOTICE": "The appointment is sooner than the automatic notice period.",
    "SIMILAR_TO_DENIED": "A similar past request was denied by staff.",
    "JEV_CONCERN": "Jev flagged a concern for staff to review.",
    "JEV_INSUFFICIENT_CONTEXT": "Jev did not have enough context to assess the request.",
    "JEV_LOW_CONFIDENCE": "Jev's confidence was below the review threshold.",
    "JEV_NO_CONCERN": "Jev reported no concern; other checks still apply.",
    "JEV_UNAVAILABLE": "Jev was unavailable, so staff review is needed.",
    "RETRIEVAL_UNAVAILABLE": "Past-call retrieval was unavailable.",
    "UNKNOWN_SERVICE": "The requested service is not available.",
    "SLOT_UNAVAILABLE": "The requested time is unavailable.",
    "PARTY_TOO_LARGE": "The group exceeds the maximum permitted size.",
    "NO_CAPACITY": "The requested slot has insufficient capacity.",
}


def reason_text(code):
    return REASONS.get(code, str(code or "Reason not recorded").replace("_", " ").capitalize())


def business_time(value):
    if not value:
        return "Not recorded"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        if parsed.tzinfo is None:
            return f"{value} (timezone not recorded)"
        return parsed.astimezone(ZoneInfo("America/Chicago")).strftime("%b %d, %Y, %I:%M %p %Z")
    except (TypeError, ValueError, AttributeError):
        return str(value)


def percentage(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1:
        return f"{value:.0%}"
    return "Not recorded"


def case_view(case):
    result = dict(case)
    snapshot = case.get("policy_snapshot") or {}
    providers = snapshot.get("providers") or {}
    skipped = case.get("decision") == "clarify"
    result["providers"] = {"chat": "Not recorded"}
    for name in ("retrieval", "jev"):
        mode = providers.get(name)
        result["providers"][name] = (
            "Not checked" if skipped and name not in providers else
            {"fake": "Fake", "live": "Live", "unset": "Unset"}.get(mode, "Not recorded")
        )
    if providers.get("retrieval") == "live" and providers.get("embeddings") == "fake":
        result["providers"]["retrieval"] = "Fake embeddings (pgvector)"
    result["checks"] = snapshot.get("checks") or []
    result["precedents"] = snapshot.get("precedents") or []
    result["providers_skipped"] = skipped
    result["jev_reasons"] = [
        reason for reason in (case.get("reasons") or [])
        if str(reason.get("code", "")).startswith("JEV_")
    ]
    return result
