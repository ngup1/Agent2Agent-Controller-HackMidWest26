"""POST /chat through the real gate and order connector on PostgreSQL.

The chat model is an explicit fake; retrieval and Jev run in their labelled
fake modes. No inference request is made.
"""
import pytest
from fastapi.testclient import TestClient

from backend.config import DEMO_BUSINESS_ID, Settings
from backend.database.conversation_repository import PostgresConversationRepository
from backend.main import create_app
from backend.services.booking_chat import ChatTurn
from backend.services.conversation import CALLBACK_LINE
from backend.services.receptionist_runtime import GateAdapter, OrderExecutor

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
def shop(db):  # noqa: F811
    chat = ScriptedChat()
    client = TestClient(create_app(
        SETTINGS,
        repository=PostgresConversationRepository(DEMO_BUSINESS_ID, db),
        chat=chat,
        gateway=GateAdapter(SETTINGS, db),
        executor=OrderExecutor(SETTINGS, db),
    ))

    def call(items, confirmation, said="I would like to order"):
        chat.proposal = {"items": items, "customer_name": "Sam"}
        first = client.post("/chat", json={"text": said}).json()
        reply = client.post("/chat", json={"text": confirmation, "interaction_id": first["interaction_id"],
                                           "conversation_token": first["conversation_token"]}).json()["reply"]
        with db() as conn:
            rows = conn.execute(
                """SELECT a.state, a.payload->'items',
                          (SELECT decision FROM security_decisions d WHERE d.action_id=a.id),
                          (SELECT status FROM human_reviews h WHERE h.action_id=a.id),
                          (SELECT total_usd::text FROM orders o WHERE o.action_id=a.id),
                          (SELECT status FROM interactions i WHERE i.id=a.interaction_id)
                   FROM agent_actions a WHERE a.interaction_id=%s""",
                (first["interaction_id"],),
            ).fetchall()
        return first["reply"], reply, rows

    return call


def test_spoken_routine_order_is_placed_once_through_gate_and_connector(shop):
    recap, reply, rows = shop(["Grilled pork banh mi", "iced coffee"], "Yes, that's correct.")
    assert recap == "1 Grilled pork banh mi and 1 Vietnamese iced coffee, $13.00 total, under Sam. Is that correct?"
    assert reply == "Your order is placed. The total is $13.00. Thank you!"
    assert rows == [("executed", ["grilled_pork", "iced_coffee"], "allow", None, "13.00", "active")]


def test_large_order_and_allergy_go_to_staff_and_unknown_item_is_refused_without_an_order(shop):
    recap, reply, rows = shop(["party tray", "party_tray"], "yeah")
    assert "$120.00 total" in recap
    assert reply == CALLBACK_LINE
    assert rows == [("pending_review", ["party_tray", "party_tray"], "escalate", "pending", None, "escalated")]

    _, reply, rows = shop(["tofu"], "yes", said="One tofu banh mi for Sam, I have a peanut allergy")
    assert reply == CALLBACK_LINE
    assert rows == [("pending_review", ["tofu"], "escalate", "pending", None, "escalated")]

    recap, reply, rows = shop(["pizza"], "yes")
    assert "total" not in recap
    assert "isn't on our menu" in reply
    assert rows == [("rejected", ["pizza"], "clarify", None, None, "active")]

    _, reply, rows = shop(["grilled_pork"], "yes but make it two")
    assert "haven't placed" in reply and rows == []
