"""Gate against real PostgreSQL: the decision row, state change and review row."""

from contextlib import nullcontext

import pytest

from backend.config import DEMO_BUSINESS_ID, Settings
from backend.database.gate_store import GateStore
from backend.database.repository import TranscriptRepository
from backend.services.gateway import evaluate, fake_assess

from .test_foundation import PROPOSAL, database  # noqa: F401

pytestmark = pytest.mark.integration
SETTINGS = Settings(retrieval_mode="fake", jev_mode="fake")


def run(database, payload, said="One grilled pork banh mi please", **kwargs):
    repo = database.repo
    interaction = repo.create_interaction(DEMO_BUSINESS_ID, "api")
    repo.save_segment(DEMO_BUSINESS_ID, interaction, said)
    repo.save_segment(DEMO_BUSINESS_ID, interaction, "Sure, what name is it under?", speaker="business_agent")
    repo.save_segment(DEMO_BUSINESS_ID, interaction, "Sam")
    action = repo.create_action(DEMO_BUSINESS_ID, interaction, payload)
    decision = evaluate(action, store=GateStore(database.connect), settings=SETTINGS, **kwargs)
    return interaction, action, decision


def state(database, action, interaction):
    conn = database.conn
    return (
        conn.execute("SELECT state FROM agent_actions WHERE id=%s", (action,)).fetchone()[0],
        conn.execute("SELECT status FROM human_reviews WHERE action_id=%s", (action,)).fetchall(),
        conn.execute("SELECT status FROM interactions WHERE id=%s", (interaction,)).fetchone()[0],
    )


@pytest.mark.parametrize("shared_transaction", [False, True], ids=["separate-transactions", "shared-transaction"])
def test_delayed_gate_uses_each_proposals_conversation_boundary(database, shared_transaction):
    before = [
        ("caller", "One grilled pork banh mi for Sam please"),
        ("business_agent", "1 Grilled pork banh mi, $8.50 total, under Sam. Is that correct?"),
        ("caller", "Yes"),
    ]
    after = [
        ("caller", "Actually, make it two party trays"),
        ("business_agent", "2 Party tray, $120.00 total, under Sam. Is that correct?"),
        ("caller", "Yes, two trays"),
    ]
    assessed, retrieved = [], []

    def assess(proposal, transcript, checks, precedents):
        assessed.append([(turn["speaker"], turn["content"]) for turn in transcript])
        return fake_assess(proposal, transcript, checks, precedents)

    def retrieve(business_id, text, exclude_interaction_id):
        retrieved.append(text)
        return []

    with database.connect() as conn:
        connect = (lambda: nullcontext(conn)) if shared_transaction else database.connect
        repo, store = TranscriptRepository(connect), GateStore(connect)
        with conn.transaction() if shared_transaction else nullcontext():
            interaction = repo.create_interaction(DEMO_BUSINESS_ID, "api")
            for speaker, content in before:
                repo.save_segment(DEMO_BUSINESS_ID, interaction, content, speaker=speaker)
            first = repo.create_action(DEMO_BUSINESS_ID, interaction, PROPOSAL)
            for speaker, content in after:
                repo.save_segment(DEMO_BUSINESS_ID, interaction, content, speaker=speaker)
            second = repo.create_action(DEMO_BUSINESS_ID, interaction, PROPOSAL | {"items": ["party_tray", "party_tray"]})

            # Both assessments run after the later conversation and proposal exist.
            for action, expected_decision in [(first, "allow"), (second, "escalate")]:
                decision = evaluate(action, store=store, settings=SETTINGS,
                                    retrieve=retrieve, assess=assess)
                assert decision.decision == expected_decision

    assert assessed == [before, before + after]
    assert retrieved == [" ".join(content for speaker, content in turns if speaker == "caller")
                         for turns in (before, before + after)]


def test_gate_persists_one_decision_and_moves_the_action(database):
    conn = database.conn
    seen = {}

    def retrieve(business_id, text, exclude_interaction_id):
        seen.update(text=text, exclude=exclude_interaction_id)
        return []

    interaction, action, decision = run(database, PROPOSAL, retrieve=retrieve)
    assert decision.decision == "allow"
    assert seen == {"text": "One grilled pork banh mi please Sam", "exclude": interaction}
    assert state(database, action, interaction) == ("allowed", [], "active")
    row = conn.execute(
        "SELECT interaction_id, decision, risk_level, reasons, policy_snapshot FROM security_decisions WHERE action_id=%s",
        (action,),
    ).fetchall()
    assert row == [(interaction, "allow", "low", decision.reasons, decision.policy_snapshot)]

    # Evaluating again returns the stored decision and writes nothing.
    again = evaluate(action, store=GateStore(database.connect), settings=Settings())
    assert again == decision
    assert conn.execute("SELECT count(*) FROM security_decisions WHERE action_id=%s", (action,)).fetchone()[0] == 1

    interaction, action, decision = run(database, PROPOSAL | {"items": ["party_tray", "party_tray"]})
    assert (decision.decision, decision.reasons[0]["code"]) == ("escalate", "ORDER_OVER_LIMIT")
    assert decision.reasons[0]["evidence"] == {"limit": "100.00", "actual": "120.00"}
    assert state(database, action, interaction) == ("pending_review", [("pending",)], "escalated")

    interaction, action, decision = run(database, PROPOSAL, said="One grilled pork. I have a peanut allergy.")
    assert decision.reasons[0] == {"code": "KEYWORD_RULE", "source": "rule", "effect": "escalate",
                                   "evidence": {"label": "Allergy mention", "matched": ["allergy", "peanut"]}}
    assert state(database, action, interaction) == ("pending_review", [("pending",)], "escalated")

    interaction, action, decision = run(database, PROPOSAL | {"items": ["pizza"]})
    assert (decision.decision, decision.reasons[0]["code"]) == ("clarify", "UNKNOWN_ITEM")
    assert state(database, action, interaction) == ("rejected", [], "active")
    assert conn.execute("SELECT count(*) FROM orders").fetchone()[0] == 0


def test_gate_reads_the_stored_rules_and_menu_on_every_decision(database):
    """What stage 2's settings page will edit: no code change, next decision differs."""
    conn = database.conn
    assert run(database, PROPOSAL)[2].decision == "allow"

    conn.execute(
        """UPDATE business_policies SET booking_rules = jsonb_set(booking_rules, '{max_order_total_auto}', '5')
           WHERE business_id=%s""", (DEMO_BUSINESS_ID,))
    _, _, decision = run(database, PROPOSAL)
    assert decision.reasons[0]["evidence"] == {"limit": "5.00", "actual": "8.50"}
    assert decision.policy_snapshot["rules"]["max_order_total_auto"] == "5.00"

    conn.execute(
        """UPDATE business_policies SET booking_rules = booking_rules
               || '{"max_order_total_auto": 100, "keyword_rules": [{"label": "Catering", "keywords": ["office party"]}]}'
           WHERE business_id=%s""", (DEMO_BUSINESS_ID,))
    _, _, decision = run(database, PROPOSAL, said="One grilled pork for our office party, I have a peanut allergy")
    assert [item["evidence"]["label"] for item in decision.reasons if item["code"] == "KEYWORD_RULE"] == ["Catering"]

    conn.execute("UPDATE services SET price_usd=200 WHERE code='grilled_pork'")
    assert run(database, PROPOSAL)[2].reasons[0]["evidence"] == {"limit": "100.00", "actual": "200.00"}
    conn.execute("UPDATE services SET active=FALSE WHERE code='grilled_pork'")
    assert run(database, PROPOSAL)[2].reasons[0]["code"] == "UNKNOWN_ITEM"
