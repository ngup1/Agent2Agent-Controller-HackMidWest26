from contextlib import nullcontext
from unittest.mock import MagicMock, Mock

import psycopg
import pytest
from fastapi.testclient import TestClient

from backend.api.dashboard import get_review_service
from backend.config import Settings
from backend.main import create_app
from backend.services.dashboard_preview import ACTION_ID
from backend.services.dashboard_review import ReviewService, ReviewConflict, ReviewNotFound

AUTH = ("staff", "test-password")
URL = f"/dashboard/cases/{ACTION_ID}/decision"


def client(service, preview=False):
    app = create_app(Settings(dashboard_password=AUTH[1], dashboard_preview=preview))
    app.dependency_overrides[get_review_service] = lambda: service
    return TestClient(app)


@pytest.mark.parametrize("auth", [None, ("staff", "wrong")])
def test_unauthenticated_decision_never_calls_service(auth):
    service = Mock()
    assert client(service).post(URL, json={"decision": "approve"}, auth=auth).status_code == 401
    service.decide.assert_not_called()


@pytest.mark.parametrize("decision", ["approve", "deny"])
def test_authenticated_decision_redirects_to_authoritative_case(decision):
    service = Mock()
    response = client(service).post(URL, json={"decision": decision, "note": "Reviewed"}, auth=AUTH)
    assert response.status_code == 200
    assert response.json() == {"redirect": f"/dashboard/cases/{ACTION_ID}"}
    service.decide.assert_called_once_with(ACTION_ID, decision, "Reviewed")
    assert "booked" not in response.text


def test_preview_rejects_direct_post():
    service = Mock()
    assert client(service, preview=True).post(URL, json={"decision": "approve"}, auth=AUTH).status_code == 409
    service.decide.assert_not_called()


@pytest.mark.parametrize("body", [{"decision": "allow"}, {"decision": "deny", "note": "a" * 2001}])
def test_invalid_decision_does_not_mutate(body):
    service = Mock()
    assert client(service).post(URL, json=body, auth=AUTH).status_code == 422
    service.decide.assert_not_called()


@pytest.mark.parametrize("error,status", [
    (ReviewNotFound(), 404), (ReviewConflict(), 409),
    (psycopg.OperationalError("secret-connection-string"), 503),
])
def test_failure_does_not_claim_success_or_expose_connection(error, status):
    service = Mock()
    service.decide.side_effect = error
    response = client(service).post(URL, json={"decision": "approve"}, auth=AUTH)
    assert response.status_code == status
    assert "secret-connection-string" not in response.text


def service_with_records(state="pending_review", status="pending"):
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = [{"state": state}, {"id": ACTION_ID, "status": status}]
    executor = Mock()
    service = ReviewService(Settings(), lambda: nullcontext(conn), executor)
    return service, conn, executor


def test_approval_reuses_review_transaction_for_execution():
    service, conn, executor = service_with_records()
    service.decide(ACTION_ID, "approve", "  reviewed  ")
    executor.assert_called_once()
    kwargs = executor.call_args.kwargs
    assert kwargs["business_id"] == Settings().business_id
    with kwargs["connection_factory"]() as shared:
        assert shared is conn


def test_denial_does_not_execute():
    service, _, executor = service_with_records()
    service.decide(ACTION_ID, "deny")
    executor.assert_not_called()


def test_duplicate_approval_does_not_execute_again():
    service, _, executor = service_with_records(state="executed", status="approved")
    service.decide(ACTION_ID, "approve")
    executor.assert_not_called()


def test_conflicting_decision_is_rejected():
    service, _, executor = service_with_records(state="rejected", status="denied")
    with pytest.raises(ReviewConflict):
        service.decide(ACTION_ID, "approve")
    executor.assert_not_called()
