"""Offline dashboard contract checks. These do not validate PostgreSQL queries."""
from copy import deepcopy
from unittest.mock import Mock
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from backend.api.dashboard import get_repository
from backend.config import Settings
from backend.main import create_app
from backend.services.dashboard_preview import ACTION_ID, CLARIFY_ID, CASE
from backend.services.dashboard_display import business_time

AUTH = ("staff", "test-password")
PATHS = ["/dashboard", "/dashboard/queue", f"/dashboard/cases/{ACTION_ID}"]


def client(preview=True, repository=None):
    app = create_app(Settings(dashboard_password=AUTH[1], dashboard_preview=preview))
    if repository is not None:
        app.dependency_overrides[get_repository] = lambda: repository
    return TestClient(app)


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("auth", [None, ("staff", "incorrect")])
def test_every_dashboard_route_requires_password(path, auth):
    response = client().get(path, auth=auth)
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic")
    assert "Sam (sample)" not in response.text


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
    assert "The order total is above the automatic limit." in queue.text
    assert "2 × party_tray" in queue.text
    assert "/dashboard/queue" in queue.text
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
    repo.queue.return_value = [case]
    repo.recent.return_value = {"calls": [], "orders": []}
    repo.case.return_value = case
    for path in PATHS:
        response = client(repository=repo).get(path, auth=AUTH)
        assert response.status_code == 200
        assert payload not in response.text
        assert "&lt;script&gt;" in response.text


@pytest.mark.parametrize("path", PATHS)
def test_database_failure_is_not_empty_queue_or_silent_preview(path):
    repo = Mock()
    for method in (repo.queue, repo.recent, repo.case):
        method.side_effect = psycopg.OperationalError("secret-password connection failed")
    response = client(preview=False, repository=repo).get(path, auth=AUTH)
    assert response.status_code == 503
    assert "unavailable" in response.text.lower()
    assert "secret-password" not in response.text
    assert "Sam (sample)" not in response.text
    assert "No reviews recorded" not in response.text


def test_empty_queue_and_missing_case():
    repo = Mock()
    repo.queue.return_value = []
    repo.recent.return_value = {"calls": [], "orders": []}
    repo.case.return_value = None
    browser = client(preview=False, repository=repo)
    assert "No reviews recorded." in browser.get("/dashboard", auth=AUTH).text
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
    repo.queue.return_value = []
    repo.recent.return_value = {"calls": [], "orders": [placed]}
    browser = client(preview=False, repository=repo)
    page = browser.get(f"/dashboard/cases/{ACTION_ID}", auth=AUTH).text
    for expected in ["Order placed", "order-ref-1", "2 × Grilled pork banh mi", "$17.00",
                     "Allergy mention: The caller said a word on a staff-review list. Said: peanut, allergy."]:
        assert expected in page
    assert "No order record found." not in page
    home = browser.get("/dashboard", auth=AUTH).text
    assert "Sam (sample) · $17.00" in home and "2 × Grilled pork banh mi" in home


def test_a_case_stored_before_orders_existed_still_renders():
    case = deepcopy(CASE)
    case["payload"] = {"service": "haircut", "party_size": 3, "customer_name": "Old call"}
    case["policy_snapshot"].pop("order_total")
    repo = Mock()
    repo.case.return_value = case
    response = client(preview=False, repository=repo).get(f"/dashboard/cases/{ACTION_ID}", auth=AUTH)
    assert response.status_code == 200
    assert "Not recorded" in response.text and "Not computed" in response.text
