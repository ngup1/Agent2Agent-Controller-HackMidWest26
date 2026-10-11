"""Real PostgreSQL/pgvector checks. TEST_DATABASE_URL must be disposable."""

import os
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from pgvector.psycopg import register_vector
from psycopg import sql

from backend.config import DEMO_BUSINESS_ID, Settings
from backend.database.repository import TranscriptRepository
from backend.main import create_app
from backend.scripts.init_db import apply_migrations
from backend.scripts.seed import MENU, ORDER_RULES, seed

pytestmark = pytest.mark.integration
PROPOSAL = {
    "items": ["grilled_pork"],
    "customer_name": "Sam",
    "callback_number": "+15555550123",
}


@pytest.fixture
def database():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL unset; real PostgreSQL validation skipped")
    schema = "ssa001_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute("CREATE EXTENSION IF NOT EXISTS vector")
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            with psycopg.connect(dsn, autocommit=True, options=f"-c search_path={schema},public") as conn:
                apply_migrations(conn)
                seed(conn, DEMO_BUSINESS_ID)

                def connect():
                    connection = psycopg.connect(dsn, options=f"-c search_path={schema},public")
                    register_vector(connection)
                    return connection

                yield SimpleNamespace(conn=conn, connect=connect, dsn=dsn, repo=TranscriptRepository(connect))
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def menu_rows(conn):
    return conn.execute("SELECT code, name, price_usd::text, active FROM services ORDER BY code").fetchall()


def test_migrations_and_seed_repeat_without_duplicates_or_overwriting_staff(database):
    conn = database.conn
    before = menu_rows(conn)
    apply_migrations(conn)
    seed(conn, DEMO_BUSINESS_ID)
    assert menu_rows(conn) == before == sorted((code, name, price, True) for code, (name, price) in MENU.items())
    assert conn.execute("SELECT name, industry FROM businesses").fetchall() == [("Demo Banh Mi", "restaurant")]
    assert conn.execute("SELECT booking_rules FROM business_policies").fetchone()[0] == ORDER_RULES
    assert conn.execute("SELECT count(*) FROM slots").fetchone()[0] == 0  # no pickup times are seeded
    # A plain re-seed keeps what staff changed.
    conn.execute("UPDATE services SET price_usd=9.25 WHERE code='tofu'")
    conn.execute("UPDATE services SET active=FALSE WHERE code='party_tray'")
    conn.execute("UPDATE business_policies SET booking_rules=jsonb_set(booking_rules, '{max_order_total_auto}', '50')")
    seed(conn, DEMO_BUSINESS_ID)
    assert ("tofu", "Tofu banh mi", "9.25", True) in menu_rows(conn)
    assert ("party_tray", "Party tray", "60.00", False) in menu_rows(conn)
    assert conn.execute("SELECT booking_rules->>'max_order_total_auto' FROM business_policies").fetchone()[0] == "50"
    assert conn.execute("SELECT count(*) FROM orders").fetchone()[0] == 0


def test_seed_reset_replaces_a_salon_setup_without_deleting_calls(database):
    """The pivot path on a database that was seeded as the salon."""
    conn, repo = database.conn, database.repo
    conn.execute("UPDATE businesses SET name='Demo Salon', industry='salon'")
    conn.execute("""UPDATE business_policies SET booking_rules='{"max_party_size_auto": 1, "max_party_size_hard": 3}'""")
    conn.execute("INSERT INTO services (business_id, code, name, price_usd) VALUES (%s, 'haircut', 'Haircut', 30)",
                 (DEMO_BUSINESS_ID,))
    conn.execute("UPDATE services SET price_usd=1 WHERE code='tofu'")
    interaction = repo.create_interaction(DEMO_BUSINESS_ID)
    repo.save_segment(DEMO_BUSINESS_ID, interaction, "An earlier call")

    seed(conn, DEMO_BUSINESS_ID)  # without --reset the salon setup is left alone
    assert conn.execute("SELECT name FROM businesses").fetchone()[0] == "Demo Salon"
    assert "max_order_total_auto" not in conn.execute("SELECT booking_rules FROM business_policies").fetchone()[0]

    seed(conn, DEMO_BUSINESS_ID, reset=True)
    assert conn.execute("SELECT name, industry FROM businesses").fetchall() == [("Demo Banh Mi", "restaurant")]
    assert conn.execute("SELECT booking_rules FROM business_policies").fetchone()[0] == ORDER_RULES
    rows = menu_rows(conn)
    assert ("haircut", "Haircut", "30.00", False) in rows  # deactivated, not deleted
    assert ("tofu", "Tofu banh mi", "8.00", True) in rows
    assert sum(active for *_, active in rows) == len(MENU)
    assert conn.execute("SELECT count(*) FROM transcript_segments").fetchone()[0] == 1


def test_text_first_embedding_proposal_and_gate_evidence_roundtrip(database):
    repo, conn = database.repo, database.conn
    interaction = repo.create_interaction(DEMO_BUSINESS_ID, "phone", "synthetic-call", "+15555550123")
    segment = repo.save_segment(DEMO_BUSINESS_ID, interaction, "One grilled pork please")
    assert repo.get_segment(DEMO_BUSINESS_ID, segment)["transcript"] == "One grilled pork please"
    assert conn.execute("SELECT count(*) FROM transcript_embeddings").fetchone()[0] == 0
    assert repo.attach_embedding(DEMO_BUSINESS_ID, segment, [0.125] * 1024, "hackathon-embedding")
    assert not repo.attach_embedding(DEMO_BUSINESS_ID, segment, [0.25] * 1024, "other-model")
    assert conn.execute("SELECT embedding_model, (embedding::real[])[1] FROM transcript_embeddings").fetchone() == ("hackathon-embedding", 0.125)
    repo.save_segment(DEMO_BUSINESS_ID, interaction, "Yes", [0.1] * 1024, "caller")
    assert conn.execute("SELECT sequence_number FROM transcript_segments ORDER BY sequence_number").fetchall() == [(0,), (1,)]
    assert repo.search_similar(DEMO_BUSINESS_ID, [0.125] * 1024)
    action = repo.create_action(DEMO_BUSINESS_ID, interaction, PROPOSAL)
    other_action = repo.create_action(DEMO_BUSINESS_ID, interaction, PROPOSAL)
    rows = conn.execute("SELECT state, payload, idempotency_key FROM agent_actions").fetchall()
    assert all(state == "proposed" and payload == PROPOSAL and key for state, payload, key in rows)
    assert len({row[2] for row in rows}) == 2
    evidence = [{"code": "JEV_UNAVAILABLE", "source": "jev", "effect": "escalate", "evidence": {}}]
    snapshot = {"rules": ORDER_RULES, "checks": []}
    decision = repo.record_decision(DEMO_BUSINESS_ID, action, "escalate", "unknown", evidence, snapshot)
    assert conn.execute("SELECT interaction_id, action_id, decision, risk_level, reasons, policy_snapshot FROM security_decisions WHERE id=%s", (decision,)).fetchone() == (interaction, action, "escalate", "unknown", evidence, snapshot)
    assert conn.execute("SELECT state FROM agent_actions WHERE id=%s", (action,)).fetchone()[0] == "proposed"
    for callback in (
        lambda: repo.save_segment(uuid4(), interaction, "no"),
        lambda: repo.attach_embedding(uuid4(), segment, [0.1] * 1024),
        lambda: repo.create_action(uuid4(), interaction, PROPOSAL),
        lambda: repo.record_decision(uuid4(), other_action, "allow", "low", [], {}),
    ):
        with pytest.raises(ValueError, match="Unknown"):
            callback()


def test_repositories_participate_in_service_transaction_rollback(database):
    with database.connect() as conn:
        repo = TranscriptRepository(lambda: nullcontext(conn))
        with pytest.raises(RuntimeError, match="abort"):
            with conn.transaction():
                interaction = repo.create_interaction(DEMO_BUSINESS_ID)
                repo.save_segment(DEMO_BUSINESS_ID, interaction, "Synthetic")
                action = repo.create_action(DEMO_BUSINESS_ID, interaction, PROPOSAL)
                repo.record_decision(DEMO_BUSINESS_ID, action, "allow", "low", [], {})
                raise RuntimeError("abort")
    assert database.conn.execute("SELECT count(*) FROM interactions").fetchone()[0] == 0
    assert database.conn.execute("SELECT count(*) FROM security_decisions").fetchone()[0] == 0


def test_schema_vocabulary_and_one_order_per_action(database):
    repo, conn = database.repo, database.conn
    for channel in ("web", "phone", "api"):
        interaction = repo.create_interaction(DEMO_BUSINESS_ID, channel)
    action = repo.create_action(DEMO_BUSINESS_ID, interaction, PROPOSAL)
    for state in ("proposed", "allowed", "pending_review", "rejected", "executed", "failed"):
        conn.execute("UPDATE agent_actions SET state=%s WHERE id=%s", (state, action))
    for decision in ("allow", "escalate", "clarify"):
        repo.record_decision(DEMO_BUSINESS_ID, action, decision, "low", [], {})
    review = conn.execute("INSERT INTO human_reviews (action_id) VALUES (%s) RETURNING id", (action,)).fetchone()[0]
    for status in ("pending", "approved", "denied"):
        conn.execute("UPDATE human_reviews SET status=%s WHERE id=%s", (status, review))
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE agent_actions SET state='approved' WHERE id=%s", (action,))
    insert = """INSERT INTO orders (business_id, action_id, items, total_usd, customer_name)
                VALUES (%s, %s, '[{"code": "tofu", "name": "Tofu banh mi", "price": "8.00"}]', 8.00, 'Sam')"""
    conn.execute(insert, (DEMO_BUSINESS_ID, action))
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(insert, (DEMO_BUSINESS_ID, action))
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE orders SET items='[]'")
    with pytest.raises(ValueError, match="place_order"):
        repo.create_action(DEMO_BUSINESS_ID, interaction, PROPOSAL, action_type="book_appointment")
    assert conn.execute("SELECT count(*) FROM orders").fetchone()[0] == 1


def test_health_reports_real_database_reachable(database):
    response = TestClient(create_app(Settings(database_url=database.dsn))).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "reachable"}
