from datetime import datetime, timezone
from uuid import uuid4

import pytest
import psycopg

from backend.config import DEMO_BUSINESS_ID
from backend.connectors.booking import (
    ActionNotExecutableError,
    execute,
    list_slots,
)
from backend.database.repository import TranscriptRepository

pytestmark = pytest.mark.integration

PROPOSAL = {
    "service": "haircut",
    "start_time": "2026-10-11T15:00:00-05:00",
    "party_size": 1,
    "customer_name": "Sam",
    "callback_number": "+15555550123",
}


def test_action_state_execution_guard(database):
    """AC 3: Proposed, rejected, or unapproved actions cannot be executed."""
    repo, conn = database.repo, database.conn
    interaction = repo.create_interaction(DEMO_BUSINESS_ID)
    action = repo.create_action(DEMO_BUSINESS_ID, interaction, PROPOSAL)

    # 1. State 'proposed' -> fails
    with pytest.raises(ActionNotExecutableError):
        execute(action, DEMO_BUSINESS_ID, database.connect)

    # 2. State 'rejected' -> fails
    conn.execute("UPDATE agent_actions SET state='rejected' WHERE id=%s", (action,))
    with pytest.raises(ActionNotExecutableError):
        execute(action, DEMO_BUSINESS_ID, database.connect)

    # 3. State 'pending_review' with review still 'pending' -> fails
    conn.execute("UPDATE agent_actions SET state='pending_review' WHERE id=%s", (action,))
    conn.execute("INSERT INTO human_reviews (action_id, status) VALUES (%s, 'pending')", (action,))
    with pytest.raises(ActionNotExecutableError):
        execute(action, DEMO_BUSINESS_ID, database.connect)

    # 4. State 'pending_review' with review 'approved' -> succeeds!
    conn.execute("UPDATE human_reviews SET status='approved' WHERE action_id=%s", (action,))
    result = execute(action, DEMO_BUSINESS_ID, database.connect)
    assert result["status"] == "executed"
    assert conn.execute("SELECT state FROM agent_actions WHERE id=%s", (action,)).fetchone()[0] == "executed"


def test_idempotent_double_execution(database):
    """AC 1: Executing the same action twice yields exactly one booking."""
    repo, conn = database.repo, database.conn
    interaction = repo.create_interaction(DEMO_BUSINESS_ID)
    action = repo.create_action(DEMO_BUSINESS_ID, interaction, PROPOSAL)
    conn.execute("UPDATE agent_actions SET state='allowed' WHERE id=%s", (action,))

    res1 = execute(action, DEMO_BUSINESS_ID, database.connect)
    res2 = execute(action, DEMO_BUSINESS_ID, database.connect)

    assert res1["status"] == "executed"
    assert res2["status"] == "executed"
    assert res1["booking_id"] == res2["booking_id"]
    assert res2["already_existed"] is True

    # Exactly 1 row in bookings
    count = conn.execute("SELECT count(*) FROM bookings WHERE action_id=%s", (action,)).fetchone()[0]
    assert count == 1


def test_full_slot_fails_cleanly(database):
    """AC 2: A full slot fails cleanly with state failed and a reason."""
    repo, conn = database.repo, database.conn
    interaction = repo.create_interaction(DEMO_BUSINESS_ID)

    # Set slot capacity to 1
    slot_id = conn.execute(
        """SELECT s.id FROM slots s
           JOIN services srv ON s.service_id = srv.id
           WHERE srv.code='haircut' AND s.start_time=%s""",
        (datetime.fromisoformat("2026-10-11T15:00:00-05:00"),),
    ).fetchone()[0]
    conn.execute("UPDATE slots SET capacity=1 WHERE id=%s", (slot_id,))

    # First booking uses capacity 1
    action1 = repo.create_action(DEMO_BUSINESS_ID, interaction, PROPOSAL)
    conn.execute("UPDATE agent_actions SET state='allowed' WHERE id=%s", (action1,))
    res1 = execute(action1, DEMO_BUSINESS_ID, database.connect)
    assert res1["status"] == "executed"

    # Second booking attempts to book the now-full slot
    action2 = repo.create_action(DEMO_BUSINESS_ID, interaction, PROPOSAL)
    conn.execute("UPDATE agent_actions SET state='allowed' WHERE id=%s", (action2,))
    res2 = execute(action2, DEMO_BUSINESS_ID, database.connect)

    assert res2["status"] == "failed"
    assert res2["reason"] == "SLOT_FULL"
    assert conn.execute("SELECT state FROM agent_actions WHERE id=%s", (action2,)).fetchone()[0] == "failed"


def test_bookings_persist(database):
    """AC 4: Bookings persist in database."""
    repo, conn = database.repo, database.conn
    interaction = repo.create_interaction(DEMO_BUSINESS_ID)
    action = repo.create_action(DEMO_BUSINESS_ID, interaction, PROPOSAL)
    conn.execute("UPDATE agent_actions SET state='allowed' WHERE id=%s", (action,))

    res = execute(action, DEMO_BUSINESS_ID, database.connect)
    booking_id = res["booking_id"]

    # Verify directly via new connection
    with database.connect() as new_conn:
        row = new_conn.execute("SELECT id, customer_name FROM bookings WHERE id=%s", (booking_id,)).fetchone()
        assert row is not None
        assert row[1] == "Sam"
