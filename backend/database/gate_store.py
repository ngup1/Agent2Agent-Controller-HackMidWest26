"""Reads and the single write transaction used by the gate (SSA-010)."""
from __future__ import annotations

from uuid import UUID

from psycopg.types.json import Jsonb

from backend.database.repository import _connect
from backend.domain.gate import GateDecision

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
        """The proposal, conversation through its creation, caller-only text and rules."""
        with self.connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT a.interaction_id, a.action_type, a.payload, a.state, p.booking_rules, a.created_at
                       FROM agent_actions a
                       LEFT JOIN business_policies p ON p.business_id=a.business_id
                       WHERE a.id=%s AND a.business_id=%s""",
                    (action_id, business_id),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                cur.execute(
                    """SELECT speaker, content, created_at FROM transcript_segments
                       WHERE interaction_id=%s AND created_at<=%s ORDER BY sequence_number""",
                    (row[0], row[5]),
                )
                transcript = [{"speaker": speaker, "content": content, "recorded_at": created_at.isoformat()}
                              for speaker, content, created_at in cur.fetchall()]
                caller_text = " ".join(turn["content"] for turn in transcript if turn["speaker"] == "caller")
                return {
                    "interaction_id": row[0],
                    "action_type": row[1],
                    "payload": row[2],
                    "state": row[3],
                    "booking_rules": row[4] or {},
                    "caller_text": caller_text,
                    "transcript": transcript,
                    "decision": _stored_decision(cur, action_id),
                }

    def menu(self, business_id: UUID) -> dict:
        """Active menu items by code, with the display name and current price."""
        with self.connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT code, name, price_usd FROM services WHERE business_id=%s AND active",
                    (business_id,),
                )
                return {code: {"name": name, "price": price} for code, name, price in cur.fetchall()}

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
