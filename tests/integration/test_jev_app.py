"""SSA-009 through /chat, PostgreSQL and dashboard; explicit fake inference."""
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.domain.order_proposal import parse_order_arguments
from backend.domain.supervision import QUESTIONS
from backend.main import create_app
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
def shop(database):
    chat = FakeChat()
    client = TestClient(create_app(SETTINGS, chat=chat, connection_factory=database.connect))
    return client, chat


def scripted_proposal(chat, items):
    chat.responses.append(ChatTurn(proposal=parse_order_arguments({"items": items, "customer_name": "Sam"})))


def request_order(client, chat, items=("grilled_pork",)):
    scripted_proposal(chat, list(items))
    first = client.post("/chat", json={"text": "One grilled pork banh mi under Sam."})
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
def test_three_jev_answers_are_persisted_rendered_and_control_the_order(shop, database, monkeypatch, question_id, answer, expected_code):
    client, chat = shop
    sdk = FakeClient()
    if question_id:
        sdk.answers[question_id] = answer
    monkeypatch.setattr("backend.providers.jev._client", lambda settings: sdk)
    session, reply = request_order(client, chat)
    if question_id:
        assert reply == CALLBACK_LINE
    else:
        assert reply.startswith("Your order is placed")
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
        else:
            assert reason["evidence"]["verdict"] == "UNAVAILABLE"
            assert reason["evidence"]["confidence"] is None
    assert database.conn.execute("SELECT count(*) FROM orders WHERE action_id=%s", (action,)).fetchone()[0] == int(question_id is None)
    page = client.get(f"/dashboard/cases/{action}", auth=AUTH).text
    assert expected_code in page and "jev-test" in page and "Jev: Live" in page
    assert page.count("<dt>Verdict</dt><dd>UNAVAILABLE</dd>") == int(expected_code == "JEV_UNAVAILABLE")
    for question in QUESTIONS.values():
        assert f'<h3>{question["label"]}</h3>' in page
    assert "Answer probabilities" in page and "synthetic-test-key" not in page
    (state, _), = sdk.calls
    assert [turn["speaker"] for turn in state["conversation"]] == ["caller", "business_agent", "caller"]
    assert "Is that correct?" in state["conversation"][-2]["content"]
    assert state["conversation"][-1]["content"] == "yes"
    assert all(turn["recorded_at"] for turn in state["conversation"])


def test_jev_receives_customer_correction_and_latest_confirmation(shop, monkeypatch):
    client, chat = shop
    sdk = FakeClient()
    monkeypatch.setattr("backend.providers.jev._client", lambda settings: sdk)
    scripted_proposal(chat, ["grilled_pork"])
    first = client.post("/chat", json={"text": "One grilled pork banh mi under Sam."}).json()
    session = {key: first[key] for key in ("interaction_id", "conversation_token")}
    correction = "Actually, I want something else."
    assert client.post("/chat", json=session | {"text": correction}).status_code == 200
    scripted_proposal(chat, ["tofu", "tofu"])
    updated = "Two tofu banh mi under Sam."
    recap = client.post("/chat", json=session | {"text": updated}).json()["reply"]
    assert "Is that correct?" in recap and not sdk.calls
    reply = client.post("/chat", json=session | {"text": "yes"}).json()["reply"]
    assert reply == "Your order is placed. The total is $16.00. Thank you!"
    (state, _), = sdk.calls
    assert state["proposed_order"] == {"items": ["tofu", "tofu"], "total_usd": "16.00"}
    assert [turn["content"] for turn in state["conversation"]][-3:] == [updated, recap, "yes"]
    assert any(turn["content"] == correction for turn in state["conversation"])


def test_missing_key_is_visible_on_case_and_creates_no_order(database):
    chat = FakeChat()
    client = TestClient(create_app(replace(SETTINGS, typesafe_api_key=""), chat=chat, connection_factory=database.connect))
    session, reply = request_order(client, chat)
    assert reply == CALLBACK_LINE
    action = database.conn.execute("SELECT id FROM agent_actions WHERE interaction_id=%s", (session["interaction_id"],)).fetchone()[0]
    page = client.get(f"/dashboard/cases/{action}", auth=AUTH).text
    assert "JEV_UNAVAILABLE" in page and "TYPESAFE_API_KEY is not set" in page
    assert page.count("<dt>Verdict</dt><dd>UNAVAILABLE</dd>") == 3
    assert database.conn.execute("SELECT count(*) FROM orders").fetchone()[0] == 0
