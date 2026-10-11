"""Gate decision contract from the spec's Demo scope section."""

from dataclasses import asdict, dataclass, field


def reason(code: str, source: str, effect: str, evidence: dict | None = None) -> dict:
    """One finding. Effect is `escalate`, `clarify` or `none`."""
    return {"code": code, "source": source, "effect": effect, "evidence": evidence or {}}


@dataclass(frozen=True)
class GateDecision:
    decision: str  # allow | escalate | clarify
    risk_level: str  # low | medium | high | unknown
    reasons: list[dict] = field(default_factory=list)
    policy_snapshot: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)
