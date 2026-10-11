"""Offline dashboard contract checks. These do not validate PostgreSQL queries."""
from copy import deepcopy
from unittest.mock import Mock
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from backend.api.dashboard import get_repository, get_settings_store
from backend.config import Settings
from backend.main import create_app
from backend.services.dashboard_preview import ACTION_ID, CLARIFY_ID, CASE
from backend.services.dashboard_display import board_columns, business_time
from backend.services.shop_settings import config_text, example, parse_config

AUTH = ("staff", "test-password")
PATHS = ["/dashboard", "/dashboard/board", f"/dashboard/cases/{ACTION_ID}"]


def board_row(case, **changes):
    """A board row for a stored case, in the shape the board query returns."""
    return {"interaction_id": case["interaction_id"], "channel": case["channel"],
            "callback_number": case["callback_number"], "created_at": case["created_at"],
            "recent": False, "action_id": case["action_id"], "payload": case["payload"],
            "state": case["state"], "review_status": case["review_status"],
            "reasons": case["reasons"], "order_id": None, **changes}


def board(*rows, name="Test Shop"):
    return {"business_name": name, "rows": list(rows)}


def client(preview=True, repository=None, store=None):
    app = create_app(Settings(dashboard_password=AUTH[1], dashboard_preview=preview))
    if repository is not None:
        app.dependency_overrides[get_repository] = lambda: repository
    if store is not None:
        app.dependency_overrides[get_settings_store] = lambda: store
    return TestClient(app)


@pytest.mark.parametrize("path", PATHS + ["/dashboard/settings"])
@pytest.mark.parametrize("auth", [None, ("staff", "incorrect")])
def test_every_dashboard_route_requires_password(path, auth):
    response = client().get(path, auth=auth)
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic")
    assert "Sam (sample)" not in response.text and "grilled_pork" not in response.text


def test_unset_password_disables_dashboard():
    app = create_app(Settings(dashboard_password="", dashboard_preview=True))
    assert TestClient(app).get("/dashboard", auth=("staff", "")).status_code == 503


def test_docs_remain_public():
    assert client().get("/docs").status_code == 200


def test_preview_queue_and_case_follow_gate_contract():
    browser = client()
    queue = browser.get("/dashboard", auth=AUTH)
    assert queue.status_code == 200
    assert queue.headers["cache-control"] == "no-store"
    assert "PREVIEW" in queue.text and "Sam (sample)" in queue.text
    assert "Incoming Request Logs - Demo Banh Mi (sample)" in queue.text
    assert "The order total is above the automatic limit." in queue.text
    assert "2 × party_tray" in queue.text
    assert "/dashboard/board" in queue.text and "/dashboard/settings" in queue.text
    case = browser.get(f"/dashboard/cases/{ACTION_ID}", auth=AUTH)
    assert case.status_code == 200
    for expected in ["order_total", "$120.00", "2 × party_tray", "NO_CONCERN", "91%", "87%", "42%",
                     "+15555550123", "Call transcript", "Jev: Fake",
                     "No order record found.", "Preview is read only"]:
        assert expected in case.text
    assert AUTH[1] not in case.text


def test_clarify_omits_provider_evidence_without_crashing():
    response = client().get(f"/dashboard/cases/{CLARIFY_ID}", auth=AUTH)
    assert response.status_code == 200
    for expected in ["Jev: Not checked", "no retrieval was run", "none captured", "No staff review"]:
        assert expected in response.text


def test_untrusted_evidence_is_escaped_in_all_views():
    case = deepcopy(CASE)
    payload = '<script>alert("unsafe")</script>'
    case["payload"]["customer_name"] = payload
    case["transcript"][0]["content"] = payload
    case["policy_snapshot"]["precedents"][0]["summary"] = payload
    case["note"] = payload
    case["reasons"][0]["evidence"]["label"] = payload
    repo = Mock()
    case["reasons"][0]["effect"] = "escalate"
    repo.board.return_value = board(board_row(case), name=payload)
    repo.case.return_value = dict(case, business_name=payload)
    for path in PATHS:
        response = client(repository=repo).get(path, auth=AUTH)
        assert response.status_code == 200
        assert payload not in response.text
        assert "&lt;script&gt;" in response.text


@pytest.mark.parametrize("path", PATHS)
def test_database_failure_is_not_empty_queue_or_silent_preview(path):
    repo = Mock()
    for method in (repo.board, repo.case):
        method.side_effect = psycopg.OperationalError("secret-password connection failed")
    response = client(preview=False, repository=repo).get(path, auth=AUTH)
    assert response.status_code == 503
    assert "unavailable" in response.text.lower()
    assert "secret-password" not in response.text
    assert "Sam (sample)" not in response.text
    assert "Nothing needs action" not in response.text


def test_empty_queue_and_missing_case():
    repo = Mock()
    repo.board.return_value = board()
    repo.case.return_value = None
    browser = client(preview=False, repository=repo)
    home = browser.get("/dashboard", auth=AUTH).text
    for expected in ["No calls in progress.", "No fulfilled requests yet.", "Nothing needs action."]:
        assert expected in home
    assert browser.get(f"/dashboard/cases/{uuid4()}", auth=AUTH).status_code == 404


def test_approval_is_not_order_success_and_live_is_not_success():
    case = deepcopy(CASE)
    case.update(review_status="approved", state="failed")
    case["policy_snapshot"]["providers"]["jev"] = "live"
    case["reasons"] = [{"code": "JEV_UNAVAILABLE", "evidence": {"mode": "live"}}]
    repo = Mock()
    repo.case.return_value = case
    response = client(repository=repo).get(f"/dashboard/cases/{ACTION_ID}", auth=AUTH)
    assert "Jev: Live" in response.text
    assert "Jev was unavailable" in response.text
    assert "approval alone does not confirm an order" in response.text
    assert "Execution failed" in response.text
    assert "Order placed" not in response.text


def test_time_conversion_and_preview_default():
    assert business_time("2026-10-11T20:00:00Z") == "Oct 11, 2026, 03:00 PM CDT"
    assert "timezone not recorded" in business_time("2026-10-11T20:00:00")
    assert Settings().dashboard_preview is False


def test_database_mode_exposes_decision_form_only_for_pending_review():
    repo = Mock()
    case = deepcopy(CASE)
    repo.case.return_value = case
    browser = client(preview=False, repository=repo)
    response = browser.get(f"/dashboard/cases/{ACTION_ID}", auth=AUTH)
    assert response.status_code == 200
    assert 'id="decision-form"' in response.text
    assert "Approve and place order" in response.text
    case["review_status"] = "denied"
    case["state"] = "rejected"
    response = browser.get(f"/dashboard/cases/{ACTION_ID}", auth=AUTH)
    assert 'id="decision-form"' not in response.text


def test_placed_order_keyword_reason_and_recent_orders_are_shown():
    from decimal import Decimal
    from datetime import datetime
    placed = {"id": "order-ref-1", "action_id": ACTION_ID, "customer_name": "Sam (sample)",
              "items": [{"code": "grilled_pork", "name": "Grilled pork banh mi", "price": "8.50"}] * 2,
              "total_usd": Decimal("17.00"), "created_at": datetime.fromisoformat("2026-10-11T20:00:00+00:00")}
    case = deepcopy(CASE)
    case.update(review_status="approved", state="executed", order=placed)
    case["reasons"] = [{"code": "KEYWORD_RULE", "effect": "escalate", "source": "rule",
                        "evidence": {"label": "Allergy mention", "matched": ["peanut", "allergy"]}}]
    repo = Mock()
    repo.case.return_value = case
    repo.board.return_value = board(board_row(
        case, order_id=placed["id"], order_items=placed["items"],
        total_usd=placed["total_usd"], order_name=placed["customer_name"]))
    browser = client(preview=False, repository=repo)
    page = browser.get(f"/dashboard/cases/{ACTION_ID}", auth=AUTH).text
    for expected in ["Order placed", "order-ref-1", "2 × Grilled pork banh mi", "$17.00",
                     "Allergy mention: The caller said a word on a staff-review list. Said: peanut, allergy."]:
        assert expected in page
    assert "No order record found." not in page
    home = browser.get("/dashboard", auth=AUTH).text
    assert "Approved by staff. Order placed: 2 × Grilled pork banh mi · $17.00" in home
    assert "Incoming Request Logs - Test Shop" in home


def test_a_case_stored_before_orders_existed_still_renders():
    case = deepcopy(CASE)
    case["payload"] = {"service": "haircut", "party_size": 3, "customer_name": "Old call"}
    case["policy_snapshot"].pop("order_total")
    repo = Mock()
    repo.case.return_value = case
    response = client(preview=False, repository=repo).get(f"/dashboard/cases/{ACTION_ID}", auth=AUTH)
    assert response.status_code == 200
    assert "Not recorded" in response.text and "Not computed" in response.text


def test_board_sorts_calls_into_three_columns_and_drops_stale_calls():
    def row(**changes):
        return board_row(CASE, **changes)
    placed = {"order_id": "o1", "order_items": [{"code": "tofu", "name": "Tofu banh mi"}],
              "total_usd": 8, "order_name": "Ana", "state": "executed", "review_status": None}
    keyword = [{"code": "KEYWORD_RULE", "effect": "escalate", "evidence": {"label": "Allergy mention"}},
               {"code": "JEV_NO_CONCERN", "effect": "none"}]
    no_outcome = {"action_id": None, "payload": None, "state": None, "review_status": None, "reasons": None}
    columns = board_columns([
        row(**placed),
        row(reasons=keyword),
        row(review_status="denied", state="rejected"),
        row(review_status="approved", state="failed"),
        row(**no_outcome, recent=True, callback_number="+15555550199"),
        row(**no_outcome, recent=True, callback_number=None),
        row(**no_outcome, recent=False),
        row(state="rejected", review_status=None, recent=True),  # asked to clarify, still talking
    ])
    assert [(card["kind"], card["statement"]) for card in columns["fulfilled"]] == [
        ("fulfilled", "Order placed: 1 × Tofu banh mi · $8.00"),
        ("declined", "Declined by staff. No order placed. Requested: 2 × party_tray"),
    ]
    assert columns["fulfilled"][0]["who"] == "Ana · +15555550123"
    assert [card["statement"] for card in columns["needs_action"]] == [
        "Allergy mention", "The order could not be placed. Check the case."]
    assert [card["who"] for card in columns["in_progress"]] == [
        "+15555550199", "Unknown caller", "Sam (sample) · +15555550123"]
    assert all(card["statement"] == "In progress" for card in columns["in_progress"])


def test_board_colours_cards_and_links_only_decided_or_waiting_calls():
    repo = Mock()
    repo.board.return_value = board(
        board_row(CASE, reasons=[{"code": "ORDER_OVER_LIMIT", "effect": "escalate"}]),
        board_row(CASE, action_id=None, payload=None, state=None, review_status=None,
                  reasons=None, recent=True, callback_number="+15555550199"))
    page = client(preview=False, repository=repo).get("/dashboard/board", auth=AUTH).text
    assert f'class="card needs_action" href="/dashboard/cases/{ACTION_ID}"' in page
    assert '<div class="card in_progress"><strong>+15555550199</strong>' in page
    assert page.count("href=") == 1
    assert "<html" not in page


GOOD = config_text(example()["menu"], example()["rules"])


def test_settings_page_shows_stored_name_and_json():
    store = Mock()
    store.load.return_value = dict(example(), name='Pho <b>Real</b>')
    page = client(preview=False, store=store).get("/dashboard/settings", auth=AUTH)
    assert page.status_code == 200
    assert "Pho &lt;b&gt;Real&lt;/b&gt;" in page.text and "<b>Real</b>" not in page.text
    assert "grilled_pork" in page.text and "max_order_total_auto" in page.text
    assert "Restore banh mi example" in page.text


def test_valid_settings_are_saved_as_cleaned_values():
    from decimal import Decimal
    store = Mock()
    store.save.return_value = True
    response = client(preview=False, store=store).post(
        "/dashboard/settings", auth=AUTH, json={"name": "  Pho  Real ", "config": GOOD})
    assert response.status_code == 200
    name, menu, rules = store.save.call_args.args
    assert name == "Pho Real"
    assert menu["party_tray"] == {"name": "Party tray", "price": Decimal("60.00")}
    assert rules["max_order_total_auto"] == 100.0
    assert rules["keyword_rules"][0]["label"] == "Allergy mention"


@pytest.mark.parametrize("name,config,message", [
    ("Shop", "{not json", "not valid JSON"),
    ("Shop", "[]", "exactly two keys"),
    ("Shop", '{"menu": {}, "rules": {}}', "at least one item"),
    ("Shop", GOOD.replace("60.0", "-1"), "between 0 and"),
    ("Shop", GOOD.replace("60.0", '"lots"'), "must be a number"),
    ("Shop", GOOD.replace('"party_tray"', '"Party Tray!"'), "lowercase letters"),
    ("Shop", GOOD.replace('"max_order_total_auto": 100.0', '"limit": 100.0'), "max_order_total_auto"),
    ("Shop", GOOD.replace('"label": "Allergy mention"', '"label": ""'), "label"),
    ("   ", GOOD, "shop name"),
    ("x" * 81, GOOD, "shop name"),
])
def test_invalid_settings_are_rejected_and_nothing_is_saved(name, config, message):
    store = Mock()
    response = client(preview=False, store=store).post(
        "/dashboard/settings", auth=AUTH, json={"name": name, "config": config})
    assert response.status_code == 422
    assert "Nothing was saved." in response.json()["detail"] and message in response.json()["detail"]
    store.save.assert_not_called()


def test_settings_writes_need_password_database_mode_and_a_working_database():
    store = Mock()
    body = {"name": "Shop", "config": GOOD}
    for url in ("/dashboard/settings", "/dashboard/settings/restore"):
        assert client(preview=False, store=store).post(url, json=body).status_code == 401
        assert client(preview=True, store=store).post(url, json=body, auth=AUTH).status_code == 409
    store.save.assert_not_called()
    store.restore_example.assert_not_called()
    browser = client(preview=False, store=store)
    assert browser.post("/dashboard/settings/restore", auth=AUTH).status_code == 200
    store.restore_example.assert_called_once()
    store.save.side_effect = psycopg.OperationalError("secret-password failed")
    failed = browser.post("/dashboard/settings", json=body, auth=AUTH)
    assert failed.status_code == 503 and "secret-password" not in failed.text


def test_settings_json_round_trips_through_validation():
    menu, rules = parse_config(GOOD)
    assert config_text(menu, rules) == GOOD
