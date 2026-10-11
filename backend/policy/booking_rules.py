"""Deterministic booking rules. No database, clock or model access here."""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.domain.gate import reason

RULE_KEYS = (
    "max_party_size_auto",
    "max_party_size_hard",
    "min_notice_minutes_auto",
    "min_notice_minutes_hard",
)


@dataclass(frozen=True)
class SlotFacts:
    """What the calendar says about the requested service and start time."""

    service_known: bool = False
    slot_found: bool = False
    closed: bool = False
    capacity: int = 0
    booked: int = 0


@dataclass(frozen=True)
class RuleOutcome:
    result: str  # pass | escalate | clarify
    checks: list[dict]
    reasons: list[dict]


def rules_used(booking_rules: dict) -> dict:
    """The four limits the gate compares against; fails loudly if unseeded."""
    try:
        rules = {key: booking_rules[key] for key in RULE_KEYS}
    except KeyError as exc:
        raise ValueError(f"booking_rules is missing {exc.args[0]}; run the seed") from exc
    if not all(type(value) is int for value in rules.values()):
        raise ValueError("booking_rules limits must be integers")
    return rules


def parse_start_time(value, timezone_name: str = "America/Chicago") -> datetime | None:
    """Timezone-aware start, or None. A time without an offset is business-local."""
    if not isinstance(value, str):
        return None
    try:
        start = datetime.fromisoformat(value)
    except ValueError:
        return None
    if start.tzinfo is None or start.utcoffset() is None:
        start = start.replace(tzinfo=ZoneInfo(timezone_name))
    return start


def check_rules(proposal: dict, booking_rules: dict, facts: SlotFacts, now: datetime) -> RuleOutcome:
    """Compare the structured proposal with the rules; one check per rule."""
    rules = rules_used(booking_rules)
    auto_party, hard_party = rules["max_party_size_auto"], rules["max_party_size_hard"]
    auto_notice, hard_notice = rules["min_notice_minutes_auto"], rules["min_notice_minutes_hard"]
    checks, reasons = [], []

    def check(rule, limit, actual, result, code=None, evidence=None):
        checks.append({"rule": rule, "limit": limit, "actual": actual, "result": result})
        if code:
            reasons.append(reason(code, "rule", result, evidence))

    service = proposal.get("service")
    if facts.service_known:
        check("service", "an active service", service, "pass")
    else:
        check("service", "an active service", service, "clarify",
              "UNKNOWN_SERVICE", {"service": service})

    party = proposal.get("party_size")
    party_limit = f"{auto_party} automatic, {hard_party} maximum"
    party_valid = type(party) is int and 1 <= party <= hard_party
    if not party_valid:
        # The contract has no separate code for a party below one.
        check("party_size", party_limit, party, "clarify",
              "PARTY_TOO_LARGE", {"minimum": 1, "limit": hard_party, "actual": party})
    elif party > auto_party:
        check("party_size", party_limit, party, "escalate",
              "GROUP_SIZE", {"limit": auto_party, "actual": party})
    else:
        check("party_size", party_limit, party, "pass")

    start = parse_start_time(proposal.get("start_time"), booking_rules.get("timezone", "America/Chicago"))
    requested = proposal.get("start_time")
    slot_open = facts.service_known and facts.slot_found and not facts.closed
    if not facts.service_known:
        check("slot", "an open slot for the service", requested, "not_checked")
    elif start is None or not slot_open:
        problem = "invalid_time" if start is None else "closed" if facts.slot_found else "no_such_slot"
        check("slot", "an open slot for the service", requested, "clarify",
              "SLOT_UNAVAILABLE", {"start_time": requested, "problem": problem})
    else:
        check("slot", "an open slot for the service", requested, "pass")

    notice_limit = f"{auto_notice} minutes automatic, {hard_notice} minimum"
    if start is None:
        check("notice", notice_limit, None, "not_checked")
    else:
        notice = int((start - now).total_seconds() // 60)
        if notice < hard_notice:
            problem = "in_the_past" if notice < 0 else "too_soon"
            check("notice", notice_limit, notice, "clarify", "SLOT_UNAVAILABLE",
                  {"start_time": requested, "problem": problem, "limit": hard_notice, "actual": notice})
        elif notice < auto_notice:
            check("notice", notice_limit, notice, "escalate",
                  "SHORT_NOTICE", {"limit": auto_notice, "actual": notice})
        else:
            check("notice", notice_limit, notice, "pass")

    remaining = facts.capacity - facts.booked
    if not slot_open or start is None or not party_valid:
        check("capacity", "places left in the slot", None, "not_checked")
    elif remaining < party:
        check("capacity", f"{remaining} of {facts.capacity} places left", party, "clarify",
              "NO_CAPACITY", {"capacity": facts.capacity, "booked": facts.booked, "requested": party})
    else:
        check("capacity", f"{remaining} of {facts.capacity} places left", party, "pass")

    results = {item["result"] for item in checks}
    result = "clarify" if "clarify" in results else "escalate" if "escalate" in results else "pass"
    return RuleOutcome(result, checks, reasons)
