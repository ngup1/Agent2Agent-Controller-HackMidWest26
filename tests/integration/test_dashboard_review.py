"""Run only against a disposable TEST_DATABASE_URL; fixture isolates its schema."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.config import Settings, DEMO_BUSINESS_ID
from backend.database.dashboard import DashboardRepository
from backend.services.dashboard_review import ReviewService, ReviewConflict
from backend.scripts.dashboard_cases import create_cases, case_status
from backend.scripts.seed import seed
from tests.integration.test_foundation import database  # shared isolated-schema fixture

pytestmark = pytest.mark.integration


def pending_case(database):
    interaction = database.repo.create_interaction(DEMO_BUSINESS_ID, "web", callback_number="+15555550123")
    database.repo.save_segment(DEMO_BUSINESS_ID, interaction, "Synthetic order of two party trays.")
    action = database.repo.create_action(DEMO_BUSINESS_ID, interaction, {
        "items": ["party_tray", "party_tray"], "customer_name": "Test guest",
        "callback_number": "+15555550123",
    })
    database.repo.record_decision(DEMO_BUSINESS_ID, action, "escalate", "medium",
        [{"code": "ORDER_OVER_LIMIT", "effect": "escalate"}],
        {"order_total": "120.00",
         "checks": [{"rule": "order_total", "limit": "$100.00 automatic", "actual": "$120.00", "result": "escalate"}]})
    database.conn.execute("UPDATE agent_actions SET state='pending_review' WHERE id=%s", (action,))
    database.conn.execute("INSERT INTO human_reviews(action_id) VALUES (%s)", (action,))
    return action


def test_approve_once_and_read_saved_order(database):
    action = pending_case(database)
    service = ReviewService(Settings(), database.connect)
    service.decide(action, "approve")
    service.decide(action, "approve")
    repo = DashboardRepository(Settings(), database.connect)
    case = repo.case(action)
    assert case["review_status"] == "approved"
    assert case["state"] == "executed"
    assert case["order"]["customer_name"] == "Test guest"
    assert case["order"]["total_usd"] == 120
    assert [item["code"] for item in case["order"]["items"]] == ["party_tray", "party_tray"]
    assert len(case["transcript"]) == 1
    board = repo.board()
    assert board["business_name"] == "Demo Banh Mi"
    row, = [row for row in board["rows"] if row["action_id"] == action]
    assert row["review_status"] == "approved" and row["total_usd"] == 120
    assert row["order_name"] == "Test guest" and row["recent"] is True
    assert database.conn.execute("SELECT count(*) FROM orders WHERE action_id=%s", (action,)).fetchone()[0] == 1


def test_deny_creates_no_order_and_cannot_be_reversed(database):
    action = pending_case(database)
    service = ReviewService(Settings(), database.connect)
    service.decide(action, "deny", "Denied synthetic request")
    with pytest.raises(ReviewConflict):
        service.decide(action, "approve")
    case = DashboardRepository(Settings(), database.connect).case(action)
    assert case["review_status"] == "denied"
    assert case["state"] == "rejected"
    assert case["order"] is None


def test_concurrent_approval_produces_one_order(database):
    action = pending_case(database)
    service = ReviewService(Settings(), database.connect)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: service.decide(action, "approve"), range(2)))
    assert results == [None, None]
    assert database.conn.execute("SELECT count(*) FROM orders WHERE action_id=%s", (action,)).fetchone()[0] == 1


def test_execution_error_rolls_back_review(database):
    action = pending_case(database)
    def fail(*args, **kwargs):
        raise RuntimeError("Synthetic execution failure")
    with pytest.raises(RuntimeError):
        ReviewService(Settings(), database.connect, fail).decide(action, "approve")
    case = DashboardRepository(Settings(), database.connect).case(action)
    assert case["review_status"] == "pending"
    assert case["state"] == "pending_review"
    assert case["order"] is None


def test_manual_case_command_is_repeatable_and_results_are_persisted(database):
    seed(database.conn, DEMO_BUSINESS_ID)
    # CLI uses one transaction; mirror that here for the fixture helper.
    with database.connect() as conn:
        create_cases(conn, DEMO_BUSINESS_ID, "test-batch")
        create_cases(conn, DEMO_BUSINESS_ID, "test-batch")
    rows = case_status(database.conn, DEMO_BUSINESS_ID, "test-batch")
    assert len(rows) == 2
    assert all(row[1:4] == ("pending", "pending_review", 0) for row in rows)
    service = ReviewService(Settings(), database.connect)
    service.decide(rows[0][4], "approve")
    service.decide(rows[1][4], "deny")
    rows = case_status(database.conn, DEMO_BUSINESS_ID, "test-batch")
    assert rows[0][1:4] == ("approved", "executed", 1)
    assert rows[1][1:4] == ("denied", "rejected", 0)
    with database.connect() as conn:
        create_cases(conn, DEMO_BUSINESS_ID, "test-batch")
    assert case_status(database.conn, DEMO_BUSINESS_ID, "test-batch") == rows


def test_settings_save_changes_what_the_gate_and_receptionist_read_and_can_be_restored(database):
    from decimal import Decimal
    from backend.database.gate_store import GateStore
    from backend.database.shop_settings import ShopSettingsStore
    from backend.services.shop_settings import config_text, example, parse_config

    store = ShopSettingsStore(Settings(), database.connect)
    assert store.load() == example()
    menu, rules = parse_config(config_text(
        {"tofu": {"name": "Tofu banh mi", "price": "8.25"}, "pho": {"name": "Pho", "price": "12"}},
        {"max_order_total_auto": 40, "keyword_rules": []}))
    assert store.save("Pho Real", menu, rules) is True
    saved = store.load()
    assert saved["name"] == "Pho Real" and saved["rules"] == {"max_order_total_auto": 40.0, "keyword_rules": []}
    assert saved["menu"] == {"pho": {"name": "Pho", "price": Decimal("12.00")},
                             "tofu": {"name": "Tofu banh mi", "price": Decimal("8.25")}}
    assert GateStore(database.connect).menu(DEMO_BUSINESS_ID) == saved["menu"]
    assert DashboardRepository(Settings(), database.connect).board()["business_name"] == "Pho Real"
    # Removed items are deactivated, not deleted.
    assert database.conn.execute("SELECT active FROM services WHERE code='party_tray'").fetchone() == (False,)
    store.restore_example()
    restored = store.load()
    assert restored["name"] == "Demo Banh Mi" and restored["rules"] == example()["rules"]
    assert set(restored["menu"]) == set(example()["menu"])  # the added item is switched off again
