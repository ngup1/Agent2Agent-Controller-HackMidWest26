"""Actual PostgreSQL tests, skipped unless TEST_DATABASE_URL is supplied."""
from concurrent.futures import ThreadPoolExecutor
import os
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")
pgvector = pytest.importorskip("pgvector")
from psycopg import sql
from pgvector.psycopg import register_vector

from backend.config import DEMO_BUSINESS_ID
from backend.database.conversation_repository import PostgresConversationRepository
from backend.scripts.init_db import apply_migrations
from backend.scripts.seed import seed

pytestmark = pytest.mark.integration


@pytest.fixture
def db():
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Real PostgreSQL requires TEST_DATABASE_URL")
    schema = "ssa014_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute("CREATE EXTENSION IF NOT EXISTS vector")
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            options = f"-c search_path={schema},public"
            with psycopg.connect(dsn, autocommit=True, options=options) as migrator:
                apply_migrations(migrator)
                seed(migrator, DEMO_BUSINESS_ID)

            def connect():
                conn = psycopg.connect(dsn, options=options)
                register_vector(conn)
                return conn

            yield connect
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


PAYLOAD = {"items": ["grilled_pork", "iced_coffee"], "customer_name": "Sam"}


def test_pending_proposal_is_durable_and_one_confirmation_creates_one_action(db):
    repo = PostgresConversationRepository(DEMO_BUSINESS_ID, db)
    interaction_id = repo.create_interaction(DEMO_BUSINESS_ID, "api")
    assert repo.get_interaction(interaction_id)["channel"] == "api"
    repo.save_transcript_segment(interaction_id, "caller", "One grilled pork and an iced coffee")
    repo.save_transcript_segment(interaction_id, "business_agent", "Is that correct?")
    assert [x["role"] for x in repo.get_recent_turns(interaction_id)] == ["user", "assistant"]
    repo.store_pending_proposal(interaction_id, PAYLOAD)
    # Proposals are not actions until the later confirmation turn.
    with db() as conn:
        assert conn.execute("SELECT count(*) FROM agent_actions").fetchone()[0] == 0
    assert repo.get_pending_proposal(interaction_id)["items"] == ["grilled_pork", "iced_coffee"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(lambda _: repo.confirm_pending_proposal(interaction_id), range(2)))
    assert sum(action is not None for action in claimed) == 1
    assert repo.get_pending_proposal(interaction_id) is None
    with db() as conn:
        assert conn.execute("SELECT count(*) FROM agent_actions").fetchone()[0] == 1
        state, kind, payload = conn.execute("SELECT state, action_type, payload FROM agent_actions").fetchone()
        assert (state, kind) == ("proposed", "place_order")
        assert payload["items"] == ["grilled_pork", "iced_coffee"]
    # Works again for a separate explicitly proposed and confirmed request.
    repo.store_pending_proposal(interaction_id, PAYLOAD)
    assert repo.confirm_pending_proposal(interaction_id) is not None
    with db() as conn:
        assert conn.execute("SELECT count(*) FROM agent_actions").fetchone()[0] == 2


def test_pending_clearing_and_callback_fallback(db):
    repo = PostgresConversationRepository(DEMO_BUSINESS_ID, db)
    call = repo.create_interaction(DEMO_BUSINESS_ID, "phone", "synthetic-call-id", "+15555550123")
    repo.store_pending_proposal(call, PAYLOAD)
    repo.clear_pending_proposal(call)
    assert repo.confirm_pending_proposal(call) is None
    repo.store_pending_proposal(call, PAYLOAD)
    action = repo.confirm_pending_proposal(call)
    with db() as conn:
        payload = conn.execute("SELECT payload FROM agent_actions WHERE id=%s", (action,)).fetchone()[0]
    assert payload["callback_number"] == "+15555550123"
    facts = repo.get_public_business_facts()
    assert facts.startswith("Business name: Demo Banh Mi.")
    assert "Grilled pork banh mi (code grilled_pork, $8.50)" in facts
    assert repo.get_business_name() == "Demo Banh Mi"
    assert repo.get_menu()["party_tray"]["price"] == 60
    with db() as conn:
        conn.execute("UPDATE services SET active=FALSE WHERE code='party_tray'")
    assert "party_tray" not in repo.get_menu() and "Party tray" not in repo.get_public_business_facts()
    assert repo.get_order_for_action(action) is None
