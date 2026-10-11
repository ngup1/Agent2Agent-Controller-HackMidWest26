"""SSA-009 through /chat, PostgreSQL and dashboard; explicit fake inference."""
from dataclasses import replace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from backend.config import DEMO_BUSINESS_ID, Settings
from backend.domain.booking_proposal import parse_booking_arguments
from backend.domain.supervision import QUESTIONS
from backend.main import create_app
from backend.scripts.seed import seed
from backend.services.booking_chat import ChatTurn
from backend.services.conversation import CALLBACK_LINE
from tests.test_jev import FakeClient, sdk_answer
from tests.unit.fakes_ssa014 import FakeChat

pytestmark = pytest.mark.integration
SETTINGS = Settings(retrieval_mode="fake", jev_mode="live", typesafe_api_key="synthetic-test-key",
                    chat_session_secret="test-only-local-jev-session-secret",
                    dashboard_password="test-only-local-staff-password")
AUTH = ("staff", SETTINGS.dashboard_password)


@pytest.fixture
def salon(database):
    seed(database.conn, DEMO_BUSINESS_ID)
    starts = [row[0].astimezone(ZoneInfo("America/Chicago")).isoformat()
              for row in database.conn.execute(
                  """SELECT DISTINCT start_time FROM slots
                     WHERE start_time > now() + interval '1 day' ORDER BY start_time LIMIT 3""")]
    chat = FakeChat()
    client = TestClient(create_app(SETTINGS, chat=chat, connection_factory=database.connect))
    return client, chat, starts


def scripted_proposal(chat, start):
    chat.responses.append(ChatTurn(proposal=parse_booking_arguments({
        "service": "haircut", "start_time": start, "customer_name": "Sam", "party_size": 1,
    })))


def request_booking(client, chat, start):
    scripted_proposal(chat, start)
    first = client.post("/chat", json={"text": f"A haircut at {start} for one person under Sam."})
    assert first.status_code == 200 and "Is that correct?" in first.json()["reply"]
    session = {key: first.json()[key] for key in ("interaction_id", "conversation_token")}
    confirmed = client.post("/chat", json=session | {"text": "yes"})
    assert confirmed.status_code == 200
    return session, confirmed.json()["reply"]


@pytest.mark.parametrize("question_id,answer,expected_code", [
    (None, None, "JEV_NO_CONCERN"),
    ("instruction_override", sdk_answer("CONCERN"), "JEV_CONCERN"),
    ("authority_manipulation", sdk_answer("CONCERN"), "JEV_CONCERN"),
    ("intent_mismatch", sdk_answer("CONCERN"), "JEV_CONCERN"),
    ("intent_mismatch", sdk_answer("INSUFFICIENT_CONTEXT"), "JEV_INSUFFICIENT_CONTEXT"),
    ("intent_mismatch", sdk_answer(confidence=0.79), "JEV_LOW_CONFIDENCE"),
    ("intent_mismatch", None, "JEV_UNAVAILABLE"),
])
def test_three_jev_answers_are_persisted_rendered_and_control_booking(salon, database, monkeypatch, question_id, answer, expected_code):
    client, chat, starts = salon
    sdk = FakeClient()
    if question_id:
        sdk.answers[question_id] = answer
    monkeypatch.setattr("backend.providers.jev._client", lambda settings: sdk)
    session, reply = request_booking(client, chat, starts[0])
    if question_id:
        assert reply == CALLBACK_LINE
    else:
        assert reply.startswith("Your appointment is booked")
    action, reasons = database.conn.execute(
        """SELECT a.id, d.reasons FROM agent_actions a JOIN security_decisions d ON d.action_id=a.id
           WHERE a.interaction_id=%s""", (session["interaction_id"],),
    ).fetchone()
    assert len(reasons) == 3
    for reason in reasons:
        key = reason["evidence"]["question_id"]
        assert reason["code"] == (expected_code if key == question_id else "JEV_NO_CONCERN")
        assert reason["evidence"]["model"] == "jev-test"
        if reason["code"] != "JEV_UNAVAILABLE":
            assert reason["evidence"]["probabilities"] == sdk.answers[key].probabilities
    assert database.conn.execute("SELECT count(*) FROM bookings WHERE action_id=%s", (action,)).fetchone()[0] == int(question_id is None)
    page = client.get(f"/dashboard/cases/{action}", auth=AUTH).text
    assert expected_code in page and "jev-test" in page and "Jev: Live" in page
    for question in QUESTIONS.values():
        assert f'<h3>{question["label"]}</h3>' in page
    assert "Answer probabilities" in page and "synthetic-test-key" not in page
    (state, _), = sdk.calls
    assert [turn["speaker"] for turn in state["conversation"]] == ["caller", "business_agent", "caller"]
    assert "Is that correct?" in state["conversation"][-2]["content"]
    assert state["conversation"][-1]["content"] == "yes"
    assert all(turn["recorded_at"] for turn in state["conversation"])


def test_jev_receives_customer_correction_and_latest_confirmation(salon, monkeypatch):
    client, chat, starts = salon
    sdk = FakeClient()
    monkeypatch.setattr("backend.providers.jev._client", lambda settings: sdk)
    scripted_proposal(chat, starts[0])
    first = client.post("/chat", json={"text": f"A haircut at {starts[0]} for one person under Sam."}).json()
    session = {key: first[key] for key in ("interaction_id", "conversation_token")}
    correction = "Actually, I need a later time."
    assert client.post("/chat", json=session | {"text": correction}).status_code == 200
    scripted_proposal(chat, starts[2])
    updated = f"A haircut at {starts[2]} for one person under Sam."
    recap = client.post("/chat", json=session | {"text": updated}).json()["reply"]
    assert "Is that correct?" in recap and not sdk.calls
    reply = client.post("/chat", json=session | {"text": "yes"}).json()["reply"]
    assert reply.startswith("Your appointment is booked")
    (state, _), = sdk.calls
    assert state["proposed_booking"]["start_time"] == starts[2]
    assert [turn["content"] for turn in state["conversation"]][-3:] == [updated, recap, "yes"]
    assert any(turn["content"] == correction for turn in state["conversation"])


def test_missing_key_is_visible_on_case_and_creates_no_booking(database):
    seed(database.conn, DEMO_BUSINESS_ID)
    start = database.conn.execute(
        "SELECT min(start_time) FROM slots WHERE start_time > now() + interval '1 day'",
    ).fetchone()[0].isoformat()
    chat = FakeChat()
    client = TestClient(create_app(replace(SETTINGS, typesafe_api_key=""), chat=chat, connection_factory=database.connect))
    session, reply = request_booking(client, chat, start)
    assert reply == CALLBACK_LINE
    action = database.conn.execute("SELECT id FROM agent_actions WHERE interaction_id=%s", (session["interaction_id"],)).fetchone()[0]
    page = client.get(f"/dashboard/cases/{action}", auth=AUTH).text
    assert "JEV_UNAVAILABLE" in page and "TYPESAFE_API_KEY is not set" in page
    assert database.conn.execute("SELECT count(*) FROM bookings").fetchone()[0] == 0
