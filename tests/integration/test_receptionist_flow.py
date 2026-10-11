"""POST /chat through the real gate and booking calendar on PostgreSQL.

The chat model is an explicit fake; retrieval and Jev run in their labelled
fake modes. No inference request is made.
"""
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from backend.config import DEMO_BUSINESS_ID, Settings
from backend.database.conversation_repository import PostgresConversationRepository
from backend.main import create_app
from backend.scripts.seed import seed
from backend.services.booking_chat import ChatTurn
from backend.services.conversation import CALLBACK_LINE
from backend.services.receptionist_runtime import BookingExecutor, GateAdapter

from .test_ssa014_persistence import db  # noqa: F401

pytestmark = pytest.mark.integration
SETTINGS = Settings(chat_session_secret="test-only-long-and-random-demo-session-secret",
                    retrieval_mode="fake", jev_mode="fake")


class ScriptedChat:
    """Fake chat model: returns the proposal the test sets."""

    proposal = None

    async def generate(self, turns):
        return ChatTurn(proposal=self.proposal)


@pytest.fixture
def salon(db):  # noqa: F811
    with db() as conn:
        seed(conn, DEMO_BUSINESS_ID)  # slots relative to the real clock, which the gate uses
        starts = [row[0].astimezone(ZoneInfo("America/Chicago")).isoformat() for row in conn.execute(
            """SELECT DISTINCT start_time FROM slots
               WHERE start_time > now() + interval '1 day' ORDER BY start_time LIMIT 3""")]
    chat = ScriptedChat()
    client = TestClient(create_app(
        SETTINGS,
        repository=PostgresConversationRepository(DEMO_BUSINESS_ID, db),
        chat=chat,
        gateway=GateAdapter(SETTINGS, db),
        executor=BookingExecutor(SETTINGS, db),
    ))

    def call(service, party_size, start, confirmation):
        chat.proposal = {"service": service, "start_time": start, "party_size": party_size, "customer_name": "Sam"}
        first = client.post("/chat", json={"text": "I would like to book"}).json()
        reply = client.post("/chat", json={"text": confirmation, "interaction_id": first["interaction_id"],
                                           "conversation_token": first["conversation_token"]}).json()["reply"]
        with db() as conn:
            rows = conn.execute(
                """SELECT a.state, a.payload->>'service',
                          (SELECT decision FROM security_decisions d WHERE d.action_id=a.id),
                          (SELECT status FROM human_reviews h WHERE h.action_id=a.id),
                          (SELECT count(*) FROM bookings b WHERE b.action_id=a.id),
                          (SELECT status FROM interactions i WHERE i.id=a.interaction_id)
                   FROM agent_actions a WHERE a.interaction_id=%s""",
                (first["interaction_id"],),
            ).fetchall()
        return first["reply"], reply, rows

    return call, starts


def test_spoken_routine_request_books_once_through_gate_and_calendar(salon):
    call, starts = salon
    recap, reply, rows = call("Haircut", 1, starts[0], "Yes, that's correct.")
    assert recap.startswith("A haircut for 1 person")
    assert reply.startswith("Your appointment is booked")
    assert rows == [("executed", "haircut", "allow", None, 1, "active")]


def test_group_goes_to_staff_and_unknown_service_is_refused_without_booking(salon):
    call, starts = salon
    _, reply, rows = call("beard trim", 3, starts[1], "yeah")
    assert reply == CALLBACK_LINE
    assert rows == [("pending_review", "beard_trim", "escalate", "pending", 0, "escalated")]

    _, reply, rows = call("massage", 1, starts[2], "yes")
    assert "don't offer that service" in reply
    assert rows == [("rejected", "massage", "clarify", None, 0, "active")]

    _, reply, rows = call("haircut", 1, starts[2], "yes but make it two people")
    assert "haven't booked" in reply and rows == []
