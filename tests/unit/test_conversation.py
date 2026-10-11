import asyncio
from uuid import uuid4

import pytest

from backend.domain.booking_proposal import parse_booking_arguments
from backend.services.booking_chat import BookingChatError, ChatTurn
from backend.services.conversation import CALLBACK_LINE, ConversationService
from tests.unit.fakes_ssa014 import FakeChat, FakeExecutor, FakeGateway, FakeRepository


PAYLOAD = {
    "service": "haircut",
    "start_time": "2026-10-12T15:00:00-05:00",
    "party_size": 1,
    "customer_name": "Sam",
}


def setup(decision="allow", payload=None, create_booking=True, crash_after_commit=False):
    repo = FakeRepository()
    interaction = repo.create_interaction(uuid4(), channel="api")
    chat = FakeChat(ChatTurn(proposal=parse_booking_arguments(payload or PAYLOAD)))
    gate = FakeGateway(decision)
    executor = FakeExecutor(repo, create_booking=create_booking, crash_after_commit=crash_after_commit)
    service = ConversationService(repository=repo, chat=chat, gateway=gate, executor=executor)
    return service, repo, interaction, chat, gate, executor


def run(coro):
    return asyncio.run(coro)


def test_recap_then_yes_books_once_and_records_transcript():
    svc, repo, ident, chat, gate, executor = setup()
    recap = run(svc.handle_turn(ident, "Book a haircut for Sam on Monday at 3."))
    assert "Is that correct?" in recap
    assert len(repo.bookings) == 0
    assert len(gate.calls) == 0
    confirm = run(svc.handle_turn(ident, "Yes."))
    assert "booked" in confirm.lower()
    assert len(repo.actions) == len(repo.bookings) == repo.claims == 1
    assert len(gate.calls) == len(executor.calls) == len(chat.calls) == 1
    assert len(repo.transcripts[ident]) == 4
    assert [turn["role"] for turn in repo.transcripts[ident]] == ["user", "assistant", "user", "assistant"]
    # A duplicate yes creates no new action and never calls model/gateway.
    reply = run(svc.handle_turn(ident, "yes"))
    assert "waiting for confirmation" in reply
    assert len(repo.actions) == len(repo.bookings) == 1
    assert len(gate.calls) == len(executor.calls) == 1


def test_review_uses_fixed_callback_and_zero_bookings():
    svc, repo, ident, chat, gate, executor = setup("escalate", payload={**PAYLOAD, "party_size": 3})
    run(svc.handle_turn(ident, "Group haircut"))
    reply = run(svc.handle_turn(ident, "yes please"))
    assert reply == CALLBACK_LINE
    assert len(repo.actions) == 1
    assert repo.bookings == {}
    assert executor.calls == []


@pytest.mark.parametrize("code,fragment", [
    ("UNKNOWN_SERVICE", "don't offer"),
    ("NO_CAPACITY", "isn't available"),
    ("SLOT_UNAVAILABLE", "isn't available"),
    ("PARTY_TOO_LARGE", "too large"),
])
def test_clarify_never_executes(code, fragment):
    svc, repo, ident, chat, gate, executor = setup("clarify")
    gate.reasons = [{"code": code}]
    run(svc.handle_turn(ident, "Booking please"))
    reply = run(svc.handle_turn(ident, "confirm"))
    assert fragment in reply
    assert len(repo.actions) == 1
    assert len(executor.calls) == 0
    assert repo.bookings == {}


def test_no_and_modified_yes_never_books():
    for cancellation in ("no", "yes, but change it to 4 pm", "change time please"):
        svc, repo, ident, chat, gate, executor = setup()
        run(svc.handle_turn(ident, "Haircut please"))
        reply = run(svc.handle_turn(ident, cancellation))
        assert "booked" in reply or "haven't booked" in reply
        assert repo.pending == {}
        assert repo.actions == repo.bookings == {}
        assert gate.calls == executor.calls == []


def test_callback_number_stored_only_when_customer_supplies_it():
    svc, repo, ident, *_ = setup(payload={**PAYLOAD, "callback_number": "+15555550123"})
    run(svc.handle_turn(ident, "Please call me at 555-555-0123"))
    assert repo.interactions[ident]["callback_number"] == "+15555550123"


def test_transport_callback_fallback_preserved_without_model_number():
    svc, repo, ident, *_ = setup()
    repo.interactions[ident]["callback_number"] = "+15555550000"
    run(svc.handle_turn(ident, "Haircut please"))
    assert repo.interactions[ident]["callback_number"] == "+15555550000"


def test_failed_or_unverified_execution_does_not_claim_success():
    for create_booking in (False,):
        svc, repo, ident, chat, gate, executor = setup(create_booking=create_booking)
        run(svc.handle_turn(ident, "haircut"))
        reply = run(svc.handle_turn(ident, "yes"))
        assert "couldn't verify" in reply
        assert len(repo.bookings) == 0


def test_commit_then_exception_verified_from_booking_record():
    svc, repo, ident, chat, gate, executor = setup(crash_after_commit=True)
    run(svc.handle_turn(ident, "haircut"))
    reply = run(svc.handle_turn(ident, "yes"))
    assert "booked" in reply
    assert len(repo.bookings) == 1


def test_unrecognized_gateway_result_fails_closed():
    svc, repo, ident, chat, gate, executor = setup(decision="block")
    run(svc.handle_turn(ident, "haircut"))
    reply = run(svc.handle_turn(ident, "yes"))
    assert "Nothing has been booked" in reply
    assert executor.calls == []


def test_model_generated_unverified_success_is_suppressed():
    svc, repo, ident, chat, gate, executor = setup()
    chat.responses = [ChatTurn(text="Your appointment is booked and confirmed!")]
    reply = run(svc.handle_turn(ident, "Tell me when my booking is."))
    assert "haven't booked" in reply
    assert repo.bookings == {}


def test_bad_tool_response_fails_without_gateway_call():
    svc, repo, ident, chat, gate, executor = setup()
    chat.responses = [ChatTurn(proposal={**PAYLOAD, "approved": True})]
    reply = run(svc.handle_turn(ident, "haircut"))
    assert "couldn't understand" in reply
    assert repo.actions == repo.bookings == {}
    assert gate.calls == []


def test_unavailable_chat_has_safe_fallback():
    svc, repo, ident, chat, gate, executor = setup()

    class FailChat:
        async def generate(self, turns):
            raise BookingChatError("unavailable")

    svc.chat = FailChat()
    reply = run(svc.handle_turn(ident, "haircut"))
    assert "couldn't understand" in reply
    assert executor.calls == []


def test_unknown_interaction_is_not_created_from_caller_supplied_id():
    svc, repo, ident, chat, gate, executor = setup()
    with pytest.raises(ValueError, match="Unknown"):
        run(svc.handle_turn(uuid4(), "Book something"))
