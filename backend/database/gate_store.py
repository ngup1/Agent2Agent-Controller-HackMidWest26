"""Reads and the single write transaction used by the gate (SSA-010)."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from psycopg.types.json import Jsonb

from backend.database.repository import _connect
from backend.domain.gate import GateDecision
from backend.policy.booking_rules import SlotFacts

STATE_FOR_DECISION = {"allow": "allowed", "escalate": "pending_review", "clarify": "rejected"}


def _stored_decision(cur, action_id: UUID) -> GateDecision | None:
    cur.execute(
        """SELECT decision, risk_level, reasons, policy_snapshot FROM security_decisions
           WHERE action_id=%s ORDER BY id LIMIT 1""",
        (action_id,),
    )
    row = cur.fetchone()
    return GateDecision(*row) if row else None


class GateStore:
    def __init__(self, connection_factory=_connect):
        self.connection_factory = connection_factory

    def load_action(self, business_id: UUID, action_id: UUID) -> dict | None:
        """The proposal, its call's caller text, the rules, and any earlier decision."""
        with self.connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT a.interaction_id, a.action_type, a.payload, a.state, p.booking_rules
                       FROM agent_actions a
                       LEFT JOIN business_policies p ON p.business_id=a.business_id
                       WHERE a.id=%s AND a.business_id=%s""",
                    (action_id, business_id),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                cur.execute(
                    """SELECT content FROM transcript_segments
                       WHERE interaction_id=%s AND speaker='caller' ORDER BY sequence_number""",
                    (row[0],),
                )
                caller_text = " ".join(content for (content,) in cur.fetchall())
                return {
                    "interaction_id": row[0],
                    "action_type": row[1],
                    "payload": row[2],
                    "state": row[3],
                    "booking_rules": row[4] or {},
                    "caller_text": caller_text,
                    "decision": _stored_decision(cur, action_id),
                }

    def slot_facts(self, business_id: UUID, service_code, start: datetime | None) -> SlotFacts:
        if not isinstance(service_code, str):
            return SlotFacts()
        with self.connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT sl.id, sl.closed, sl.capacity,
                              (SELECT COALESCE(SUM(b.party_size), 0) FROM bookings b WHERE b.slot_id=sl.id)
                       FROM services s
                       LEFT JOIN slots sl ON sl.service_id=s.id AND sl.start_time=%s
                       WHERE s.business_id=%s AND s.code=%s AND s.active""",
                    (start, business_id, service_code),
                )
                row = cur.fetchone()
        if row is None:
            return SlotFacts()
        if row[0] is None:
            return SlotFacts(service_known=True)
        return SlotFacts(True, True, row[1], row[2], int(row[3]))

    def apply_decision(self, business_id: UUID, action_id: UUID, decision: GateDecision) -> GateDecision:
        """Record the decision and move the action in one transaction.

        Only a `proposed` action is decided. If another evaluation got there
        first, its stored decision is returned and nothing is written.
        """
        with self.connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT interaction_id, state FROM agent_actions
                       WHERE id=%s AND business_id=%s FOR UPDATE""",
                    (action_id, business_id),
                )
                row = cur.fetchone()
                if row is None:
                    raise ValueError("Unknown action for this business")
                interaction_id, state = row
                if state != "proposed":
                    stored = _stored_decision(cur, action_id)
                    if stored is None:
                        raise ValueError(f"Action is {state} with no gate decision")
                    return stored
                cur.execute(
                    """INSERT INTO security_decisions
                       (interaction_id, action_id, decision, risk_level, reasons, policy_snapshot)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (interaction_id, action_id, decision.decision, decision.risk_level,
                     Jsonb(decision.reasons), Jsonb(decision.policy_snapshot)),
                )
                cur.execute(
                    "UPDATE agent_actions SET state=%s WHERE id=%s",
                    (STATE_FOR_DECISION[decision.decision], action_id),
                )
                if decision.decision == "escalate":
                    cur.execute("INSERT INTO human_reviews (action_id) VALUES (%s)", (action_id,))
                    cur.execute("UPDATE interactions SET status='escalated' WHERE id=%s", (interaction_id,))
        return decision
