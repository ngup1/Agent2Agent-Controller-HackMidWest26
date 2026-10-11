"""App wiring checks; fake collaborators explicitly injected, no paid calls."""
import pytest
psycopg = pytest.importorskip("psycopg")

from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from backend.domain.booking_proposal import parse_booking_arguments
from backend.services.booking_chat import ChatTurn
from tests.unit.fakes_ssa014 import FakeChat, FakeExecutor, FakeGateway, FakeRepository

PAYLOAD = {
    "service": "haircut", "start_time": "2026-10-12T15:00:00-05:00",
    "party_size": 1, "customer_name": "Sam",
}
SECRET = "test-only-long-and-random-demo-session-secret"


def test_app_factory_registers_real_chat_router_with_explicit_fakes():
    repo = FakeRepository()
    client = TestClient(create_app(
        Settings(chat_session_secret=SECRET),
        repository=repo,
        chat=FakeChat(ChatTurn(proposal=parse_booking_arguments(PAYLOAD))),
        gateway=FakeGateway("allow"),
        executor=FakeExecutor(repo),
    ))
    first = client.post("/chat", json={"text": "A haircut for Sam"})
    assert first.status_code == 200
    session = first.json()
    second = client.post("/chat", json={
        "interaction_id": session["interaction_id"],
        "conversation_token": session["conversation_token"],
        "text": "yes",
    })
    assert second.status_code == 200
    assert "booked" in second.json()["reply"]
    assert len(repo.bookings) == 1


def test_default_gateway_is_the_real_gate_and_its_failure_books_nothing(monkeypatch):
    from backend.database.gate_store import GateStore
    from backend.services.receptionist_runtime import BookingExecutor, GateAdapter

    def database_down(self, business_id, action_id):
        raise ConnectionError("synthetic: database unreachable")

    monkeypatch.setattr(GateStore, "load_action", database_down)
    repo = FakeRepository()
    app = create_app(
        Settings(chat_session_secret=SECRET),
        repository=repo,
        chat=FakeChat(ChatTurn(proposal=parse_booking_arguments(PAYLOAD))),
    )
    service = app.state.conversation_service
    assert isinstance(service.gateway, GateAdapter) and isinstance(service.executor, BookingExecutor)
    client = TestClient(app)
    first = client.post("/chat", json={"text": "Haircut tomorrow for Sam"}).json()
    second = client.post("/chat", json={
        "interaction_id": first["interaction_id"],
        "conversation_token": first["conversation_token"],
        "text": "yes",
    })
    assert second.status_code == 200
    assert "Nothing is confirmed" in second.json()["reply"]
    assert not repo.bookings


def test_missing_secret_or_api_key_explicitly_disables_chat_route():
    no_secret = TestClient(create_app(Settings()))
    assert no_secret.post("/chat", json={"text": "hello"}).status_code == 503
    no_key = TestClient(create_app(Settings(chat_session_secret=SECRET)))
    assert no_key.post("/chat", json={"text": "hello"}).status_code == 503
