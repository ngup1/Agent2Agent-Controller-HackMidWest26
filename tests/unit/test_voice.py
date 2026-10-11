"""SSA-023 phone transport with explicit fakes; no Twilio or model calls.

The fixture follows Twilio's documented ConversationRelay message shapes with
synthetic identifiers; it is not a capture of a real call.
"""
import json
import time
from pathlib import Path
from xml.etree import ElementTree

import pytest
psycopg = pytest.importorskip("psycopg")

from fastapi.testclient import TestClient

from backend.api import voice
from backend.config import Settings
from backend.domain.booking_proposal import parse_booking_arguments
from backend.main import create_app
from backend.services.booking_chat import ChatTurn
from backend.services.conversation import CALLBACK_LINE
from tests.unit.fakes_ssa014 import FakeChat, FakeExecutor, FakeGateway, FakeRepository

FIXTURE = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "conversationrelay_setup_prompt.json").read_text()
)
SETUP, PARTIAL, PROMPT = FIXTURE
SECRET = "test-only-long-and-random-demo-session-secret"
PAYLOAD = {
    "service": "haircut", "start_time": "2026-10-12T15:00:00-05:00",
    "party_size": 1, "customer_name": "Sam",
}


class PhoneRepository(FakeRepository):
    def create_interaction(self, business_id, channel="api", external_reference=None,
                           callback_number=None):
        interaction_id = super().create_interaction(business_id, channel, callback_number or "")
        self.calls = getattr(self, "calls", []) + [(channel, external_reference, callback_number)]
        return interaction_id


def phone_app(decision="allow", chat=None, **settings):
    repo = PhoneRepository()
    app = create_app(
        Settings(chat_session_secret=SECRET, public_host="demo.example", **settings),
        repository=repo,
        chat=chat or FakeChat(ChatTurn(proposal=parse_booking_arguments(PAYLOAD))),
        gateway=FakeGateway(decision),
        executor=FakeExecutor(repo),
    )
    return app, repo


def prompt(text):
    return {"type": "prompt", "voicePrompt": text, "lang": "en-US", "last": True}


def test_incoming_returns_conversationrelay_twiml_for_the_configured_host():
    app, _ = phone_app()
    response = TestClient(app).post("/voice/incoming", headers={"host": "attacker.example"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/xml")
    relay = ElementTree.fromstring(response.text).find("Connect/ConversationRelay")
    assert relay.attrib["url"] == "wss://demo.example/voice/stream"
    assert relay.attrib["welcomeGreeting"] == voice.WELCOME_GREETING


def test_incoming_is_unavailable_without_a_public_host():
    app = create_app(Settings(chat_session_secret=SECRET), repository=FakeRepository(),
                     chat=FakeChat(ChatTurn(text="hi")), gateway=FakeGateway(),
                     executor=FakeExecutor(FakeRepository()))
    assert TestClient(app).post("/voice/incoming").status_code == 503


def test_public_host_rejects_a_url():
    with pytest.raises(ValueError):
        Settings(public_host="https://demo.example/voice")


def test_echo_mode_repeats_only_the_completed_utterance():
    app, repo = phone_app(voice_echo=True)
    with TestClient(app).websocket_connect("/voice/stream") as ws:
        for message in (SETUP, PARTIAL, PROMPT):
            ws.send_json(message)
        assert ws.receive_json() == {
            "type": "text", "token": "You said: A haircut for Sam tomorrow at three", "last": True,
        }
    assert not getattr(repo, "calls", [])


def test_routine_booking_by_voice_is_stored_as_a_phone_call():
    app, repo = phone_app("allow")
    with TestClient(app).websocket_connect("/voice/stream") as ws:
        for message in (SETUP, PARTIAL, PROMPT):
            ws.send_json(message)
        assert "Is that correct?" in ws.receive_json()["token"]
        ws.send_json(prompt("Yes, that's correct."))
        assert "booked" in ws.receive_json()["token"]
    assert repo.calls == [("phone", SETUP["callSid"], SETUP["from"])]
    assert len(repo.bookings) == 1


def test_escalation_speaks_the_callback_line_then_ends_the_call(monkeypatch):
    monkeypatch.setattr(voice, "SPEECH_CHARACTERS_PER_SECOND", 10_000)
    app, repo = phone_app("escalate")
    with TestClient(app).websocket_connect("/voice/stream") as ws:
        ws.send_json(SETUP)
        ws.send_json(PROMPT)
        ws.receive_json()
        ws.send_json(prompt("yes"))
        assert ws.receive_json()["token"] == CALLBACK_LINE
        assert ws.receive_json() == {"type": "end"}
    assert len(repo.bookings) == 0


def test_slow_turn_gets_the_filler_line_first(monkeypatch):
    monkeypatch.setattr(voice, "FILLER_AFTER_SECONDS", 0.05)

    class SlowChat(FakeChat):
        async def generate(self, turns):
            time.sleep(0.3)  # a blocking model call, as in the real client
            return await super().generate(turns)

    app, _ = phone_app(chat=SlowChat(ChatTurn(text="We are open nine to five.")))
    with TestClient(app).websocket_connect("/voice/stream") as ws:
        ws.send_json(SETUP)
        ws.send_json(prompt("When are you open?"))
        assert ws.receive_json()["token"] == voice.FILLER_LINE
        assert ws.receive_json()["token"] == "We are open nine to five."


def test_unconfigured_chat_speaks_the_unavailable_line_and_ends(monkeypatch):
    monkeypatch.setattr(voice, "SPEECH_CHARACTERS_PER_SECOND", 10_000)
    app = create_app(Settings(public_host="demo.example"))  # no chat secret: no service
    with TestClient(app).websocket_connect("/voice/stream") as ws:
        ws.send_json(SETUP)
        ws.send_json(PROMPT)
        assert ws.receive_json()["token"] == voice.UNAVAILABLE_LINE
        assert ws.receive_json() == {"type": "end"}
