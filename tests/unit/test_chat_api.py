"""HTTP tests use only explicit fake components, not a live database/model."""
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.chat import create_chat_router
from backend.domain.booking_proposal import parse_booking_arguments
from backend.services.booking_chat import ChatTurn
from backend.services.conversation import ConversationService
from tests.unit.fakes_ssa014 import FakeChat, FakeExecutor, FakeGateway, FakeRepository


PAYLOAD = {
    "service": "haircut",
    "start_time": "2026-10-12T15:00:00-05:00",
    "party_size": 1,
    "customer_name": "Sam",
}


def build_app(business_id=None, repo=None):
    repo = repo or FakeRepository()
    business_id = business_id or uuid4()
    chat = FakeChat(ChatTurn(proposal=parse_booking_arguments(PAYLOAD)))
    gate = FakeGateway()
    executor = FakeExecutor(repo)
    conversation = ConversationService(repository=repo, chat=chat, gateway=gate, executor=executor)
    app = FastAPI()
    app.include_router(create_chat_router(
        service=conversation,
        repository=repo,
        business_id=business_id,
        secret="a-long-test-only-secret-not-for-real-use",
    ))
    return TestClient(app), repo, business_id, gate, executor


def test_post_chat_two_turn_one_booking():
    client, repo, business_id, gate, executor = build_app()
    first = client.post("/chat", json={"text": "Book a haircut tomorrow at three."})
    assert first.status_code == 200
    first_json = first.json()
    assert "Is that correct?" in first_json["reply"]
    assert repo.bookings == {}
    assert all(v["business_id"] == business_id for v in repo.interactions.values())
    second = client.post("/chat", json={
        "text": "yes",
        "interaction_id": first_json["interaction_id"],
        "conversation_token": first_json["conversation_token"],
    })
    assert second.status_code == 200
    assert "booked" in second.json()["reply"]
    assert len(repo.bookings) == len(gate.calls) == len(executor.calls) == 1
    duplicate = client.post("/chat", json={
        "text": "yes",
        "interaction_id": first_json["interaction_id"],
        "conversation_token": first_json["conversation_token"],
    })
    assert duplicate.status_code == 200
    assert len(repo.bookings) == 1


def test_existing_id_without_token_is_invalid():
    client, _, _, _, _ = build_app()
    first = client.post("/chat", json={"text": "Book a haircut"}).json()
    response = client.post("/chat", json={"interaction_id": first["interaction_id"], "text": "yes"})
    assert response.status_code == 422


def test_guessed_or_corrupted_session_is_forbidden():
    client, _, _, _, _ = build_app()
    response = client.post("/chat", json={
        "interaction_id": str(uuid4()), "conversation_token": "not-the-token", "text": "yes"
    })
    assert response.status_code == 403


def test_second_business_cannot_replay_first_token():
    repo = FakeRepository()
    client1, _, _, _, _ = build_app(repo=repo)
    client2, _, _, _, _ = build_app(repo=repo)
    original = client1.post("/chat", json={"text": "haircut"}).json()
    attempt = client2.post("/chat", json={
        "text": "yes",
        "interaction_id": original["interaction_id"],
        "conversation_token": original["conversation_token"],
    })
    assert attempt.status_code == 403


def test_invalid_text_is_rejected():
    client, _, _, _, _ = build_app()
    assert client.post("/chat", json={"text": ""}).status_code == 422
