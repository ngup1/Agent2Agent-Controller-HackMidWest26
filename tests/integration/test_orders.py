from decimal import Decimal

import pytest

from backend.config import DEMO_BUSINESS_ID
from backend.connectors.orders import ActionNotExecutableError, execute

pytestmark = pytest.mark.integration

PROPOSAL = {
    "items": ["grilled_pork", "grilled_pork", "iced_coffee"],
    "customer_name": "Sam",
    "callback_number": "+15555550123",
}


def allowed_action(database, payload=PROPOSAL):
    interaction = database.repo.create_interaction(DEMO_BUSINESS_ID)
    action = database.repo.create_action(DEMO_BUSINESS_ID, interaction, payload)
    database.conn.execute("UPDATE agent_actions SET state='allowed' WHERE id=%s", (action,))
    return action


def test_action_state_execution_guard(database):
    """Proposed, rejected, or unapproved actions cannot be executed."""
    repo, conn = database.repo, database.conn
    interaction = repo.create_interaction(DEMO_BUSINESS_ID)
    action = repo.create_action(DEMO_BUSINESS_ID, interaction, PROPOSAL)

    with pytest.raises(ActionNotExecutableError):  # proposed
        execute(action, DEMO_BUSINESS_ID, database.connect)

    conn.execute("UPDATE agent_actions SET state='rejected' WHERE id=%s", (action,))
    with pytest.raises(ActionNotExecutableError):
        execute(action, DEMO_BUSINESS_ID, database.connect)

    conn.execute("UPDATE agent_actions SET state='pending_review' WHERE id=%s", (action,))
    conn.execute("INSERT INTO human_reviews (action_id, status) VALUES (%s, 'pending')", (action,))
    with pytest.raises(ActionNotExecutableError):
        execute(action, DEMO_BUSINESS_ID, database.connect)
    assert conn.execute("SELECT count(*) FROM orders").fetchone()[0] == 0

    conn.execute("UPDATE human_reviews SET status='approved' WHERE action_id=%s", (action,))
    result = execute(action, DEMO_BUSINESS_ID, database.connect)
    assert result["status"] == "executed"
    assert conn.execute("SELECT state FROM agent_actions WHERE id=%s", (action,)).fetchone()[0] == "executed"


def test_idempotent_double_execution(database):
    """Executing the same action twice yields exactly one order."""
    action = allowed_action(database)
    res1 = execute(action, DEMO_BUSINESS_ID, database.connect)
    res2 = execute(action, DEMO_BUSINESS_ID, database.connect)
    assert res1["status"] == res2["status"] == "executed"
    assert res1["order_id"] == res2["order_id"]
    assert (res1["already_existed"], res2["already_existed"]) == (False, True)
    assert database.conn.execute(
        "SELECT count(*) FROM orders WHERE action_id=%s", (action,)).fetchone()[0] == 1


def test_order_is_priced_from_the_menu_and_persists(database):
    action = allowed_action(database, PROPOSAL | {"total": 0.01, "prices": {"grilled_pork": 0}})
    result = execute(action, DEMO_BUSINESS_ID, database.connect)
    with database.connect() as fresh:
        row = fresh.execute(
            "SELECT customer_name, callback_number, total_usd, items FROM orders WHERE id=%s",
            (result["order_id"],),
        ).fetchone()
    assert row[:3] == ("Sam", "+15555550123", Decimal("21.50"))  # 8.50 + 8.50 + 4.50
    assert row[3] == [
        {"code": "grilled_pork", "name": "Grilled pork banh mi", "price": "8.50"},
        {"code": "grilled_pork", "name": "Grilled pork banh mi", "price": "8.50"},
        {"code": "iced_coffee", "name": "Vietnamese iced coffee", "price": "4.50"},
    ]


@pytest.mark.parametrize("payload", [
    PROPOSAL | {"items": ["grilled_pork", "pizza"]},
    PROPOSAL | {"items": []},
    {"customer_name": "Sam"},
])
def test_unusable_items_fail_cleanly_with_no_order(database, payload):
    action = allowed_action(database, payload)
    result = execute(action, DEMO_BUSINESS_ID, database.connect)
    assert (result["status"], result["reason"]) == ("failed", "UNKNOWN_ITEM")
    conn = database.conn
    assert conn.execute("SELECT state FROM agent_actions WHERE id=%s", (action,)).fetchone()[0] == "failed"
    assert conn.execute("SELECT count(*) FROM orders").fetchone()[0] == 0


def test_item_removed_from_the_menu_after_the_gate_is_not_placed(database):
    action = allowed_action(database)
    database.conn.execute("UPDATE services SET active=FALSE WHERE code='iced_coffee'")
    assert execute(action, DEMO_BUSINESS_ID, database.connect)["status"] == "failed"
    assert database.conn.execute("SELECT count(*) FROM orders").fetchone()[0] == 0


def test_a_salon_booking_action_is_never_executed_as_an_order(database):
    interaction = database.repo.create_interaction(DEMO_BUSINESS_ID)
    action = database.conn.execute(
        """INSERT INTO agent_actions (business_id, interaction_id, action_type, payload, idempotency_key, state)
           VALUES (%s, %s, 'book_appointment', '{"items": ["grilled_pork"], "customer_name": "Sam"}', 'old', 'allowed')
           RETURNING id""",
        (DEMO_BUSINESS_ID, interaction),
    ).fetchone()[0]
    with pytest.raises(ActionNotExecutableError):
        execute(action, DEMO_BUSINESS_ID, database.connect)
    assert database.conn.execute("SELECT count(*) FROM orders").fetchone()[0] == 0
