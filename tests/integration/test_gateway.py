"""Gate against real PostgreSQL: the decision row, state change and review row."""

from datetime import datetime

import pytest

from backend.config import DEMO_BUSINESS_ID, Settings
from backend.database.gate_store import GateStore
from backend.services.gateway import evaluate

from .test_foundation import CLOCK, PROPOSAL, database  # noqa: F401

pytestmark = pytest.mark.integration
SETTINGS = Settings(retrieval_mode="fake", jev_mode="fake")


def run(database, payload, **kwargs):
    repo = database.repo
    interaction = repo.create_interaction(DEMO_BUSINESS_ID, "api")
    repo.save_segment(DEMO_BUSINESS_ID, interaction, "A haircut tomorrow at three please")
    repo.save_segment(DEMO_BUSINESS_ID, interaction, "Sure, for how many?", speaker="business_agent")
    repo.save_segment(DEMO_BUSINESS_ID, interaction, "Just me")
    action = repo.create_action(DEMO_BUSINESS_ID, interaction, payload)
    decision = evaluate(action, now=CLOCK, store=GateStore(database.connect), settings=SETTINGS, **kwargs)
    return interaction, action, decision


def state(database, action, interaction):
    conn = database.conn
    return (
        conn.execute("SELECT state FROM agent_actions WHERE id=%s", (action,)).fetchone()[0],
        conn.execute("SELECT status FROM human_reviews WHERE action_id=%s", (action,)).fetchall(),
        conn.execute("SELECT status FROM interactions WHERE id=%s", (interaction,)).fetchone()[0],
    )


def test_gate_persists_one_decision_and_moves_the_action(database):
    conn = database.conn
    seen = {}

    def retrieve(business_id, text, exclude_interaction_id):
        seen.update(text=text, exclude=exclude_interaction_id)
        return []

    interaction, action, decision = run(database, PROPOSAL, retrieve=retrieve)
    assert decision.decision == "allow"
    assert seen == {"text": "A haircut tomorrow at three please Just me", "exclude": interaction}
    assert state(database, action, interaction) == ("allowed", [], "active")
    row = conn.execute(
        "SELECT interaction_id, decision, risk_level, reasons, policy_snapshot FROM security_decisions WHERE action_id=%s",
        (action,),
    ).fetchall()
    assert row == [(interaction, "allow", "low", decision.reasons, decision.policy_snapshot)]

    # Evaluating again returns the stored decision and writes nothing.
    again = evaluate(action, now=datetime.fromisoformat("2026-10-11T14:50:00-05:00"),
                     store=GateStore(database.connect), settings=Settings())
    assert again == decision
    assert conn.execute("SELECT count(*) FROM security_decisions WHERE action_id=%s", (action,)).fetchone()[0] == 1

    interaction, action, decision = run(database, PROPOSAL | {"party_size": 3})
    assert (decision.decision, decision.reasons[0]["code"]) == ("escalate", "GROUP_SIZE")
    assert state(database, action, interaction) == ("pending_review", [("pending",)], "escalated")

    interaction, action, decision = run(database, PROPOSAL | {"service": "massage"})
    assert (decision.decision, decision.reasons[0]["code"]) == ("clarify", "UNKNOWN_SERVICE")
    assert state(database, action, interaction) == ("rejected", [], "active")
    assert conn.execute("SELECT count(*) FROM bookings").fetchone()[0] == 0


def test_gate_reads_closed_slots_and_remaining_capacity(database):
    conn = database.conn
    slot = conn.execute(
        """SELECT sl.id FROM slots sl JOIN services s ON s.id=sl.service_id
           WHERE s.code='haircut' AND sl.start_time=%s""",
        (PROPOSAL["start_time"],),
    ).fetchone()[0]
    _, booked_action, _ = run(database, PROPOSAL)
    conn.execute(
        "INSERT INTO bookings (business_id, action_id, slot_id, party_size, customer_name) VALUES (%s,%s,%s,2,'Ana')",
        (DEMO_BUSINESS_ID, booked_action, slot),
    )
    _, _, decision = run(database, PROPOSAL | {"party_size": 2})
    assert decision.decision == "clarify"
    assert decision.reasons[-1] == {"code": "NO_CAPACITY", "source": "rule", "effect": "clarify",
                                    "evidence": {"capacity": 3, "booked": 2, "requested": 2}}
    assert run(database, PROPOSAL)[2].decision == "allow"  # one place is still free

    conn.execute("UPDATE slots SET closed=true WHERE id=%s", (slot,))
    _, _, decision = run(database, PROPOSAL)
    assert (decision.decision, decision.reasons[0]["evidence"]["problem"]) == ("clarify", "closed")
