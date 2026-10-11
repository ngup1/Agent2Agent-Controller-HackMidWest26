import asyncio
from uuid import uuid4

import pytest

from backend.domain.order_proposal import parse_order_arguments
from backend.services.booking_chat import BookingChatError, ChatTurn
from backend.services.conversation import CALLBACK_LINE, ConversationService
from tests.unit.fakes_ssa014 import FakeChat, FakeExecutor, FakeGateway, FakeRepository


PAYLOAD = {"items": ["grilled_pork"], "customer_name": "Sam"}


def setup(decision="allow", payload=None, create_order=True, crash_after_commit=False):
    repo = FakeRepository()
    interaction = repo.create_interaction(uuid4(), channel="api")
    chat = FakeChat(ChatTurn(proposal=parse_order_arguments(payload or PAYLOAD)))
    gate = FakeGateway(decision)
    executor = FakeExecutor(repo, create_order=create_order, crash_after_commit=crash_after_commit)
    service = ConversationService(repository=repo, chat=chat, gateway=gate, executor=executor)
    return service, repo, interaction, chat, gate, executor


def run(coro):
    return asyncio.run(coro)


def test_recap_then_yes_places_once_and_records_transcript():
    svc, repo, ident, chat, gate, executor = setup()
    recap = run(svc.handle_turn(ident, "One grilled pork banh mi for Sam."))
    assert "Is that correct?" in recap
    assert len(repo.orders) == 0
    assert len(gate.calls) == 0
    confirm = run(svc.handle_turn(ident, "Yes."))
    assert "order is placed" in confirm.lower()
    assert len(repo.actions) == len(repo.orders) == repo.claims == 1
    assert len(gate.calls) == len(executor.calls) == len(chat.calls) == 1
    assert len(repo.transcripts[ident]) == 4
    assert [turn["role"] for turn in repo.transcripts[ident]] == ["user", "assistant", "user", "assistant"]
    # A duplicate yes creates no new action and never calls model/gateway.
    reply = run(svc.handle_turn(ident, "yes"))
    assert "waiting for confirmation" in reply
    assert len(repo.actions) == len(repo.orders) == 1
    assert len(gate.calls) == len(executor.calls) == 1


def test_review_uses_fixed_callback_and_zero_orders():
    svc, repo, ident, chat, gate, executor = setup("escalate", payload={**PAYLOAD, "items": ["party_tray", "party_tray"]})
    run(svc.handle_turn(ident, "Two party trays"))
    reply = run(svc.handle_turn(ident, "yes please"))
    assert reply == CALLBACK_LINE
    assert len(repo.actions) == 1
    assert repo.orders == {}
    assert executor.calls == []


@pytest.mark.parametrize("code,fragment", [
    ("UNKNOWN_ITEM", "isn't on our menu"),
    ("EMPTY_ORDER", "don't have any items"),
    ("SOMETHING_ELSE", "can't place that order"),
])
def test_clarify_never_executes(code, fragment):
    svc, repo, ident, chat, gate, executor = setup("clarify")
    gate.reasons = [{"code": code}]
    run(svc.handle_turn(ident, "Order please"))
    reply = run(svc.handle_turn(ident, "confirm"))
    assert fragment in reply
    assert len(repo.actions) == 1
    assert len(executor.calls) == 0
    assert repo.orders == {}


def test_no_and_modified_yes_never_places():
    for cancellation in ("no", "yes, but change it to tofu", "change the order please"):
        svc, repo, ident, chat, gate, executor = setup()
        run(svc.handle_turn(ident, "Grilled pork please"))
        reply = run(svc.handle_turn(ident, cancellation))
        assert "nothing is ordered" in reply or "haven't placed" in reply
        assert repo.pending == {}
        assert repo.actions == repo.orders == {}
        assert gate.calls == executor.calls == []


def test_callback_number_stored_only_when_customer_supplies_it():
    svc, repo, ident, *_ = setup(payload={**PAYLOAD, "callback_number": "+15555550123"})
    run(svc.handle_turn(ident, "Please call me at 555-555-0123"))
    assert repo.interactions[ident]["callback_number"] == "+15555550123"


def test_transport_callback_fallback_preserved_without_model_number():
    svc, repo, ident, *_ = setup()
    repo.interactions[ident]["callback_number"] = "+15555550000"
    run(svc.handle_turn(ident, "Grilled pork please"))
    assert repo.interactions[ident]["callback_number"] == "+15555550000"


def test_failed_or_unverified_execution_does_not_claim_success():
    for create_order in (False,):
        svc, repo, ident, chat, gate, executor = setup(create_order=create_order)
        run(svc.handle_turn(ident, "grilled pork"))
        reply = run(svc.handle_turn(ident, "yes"))
        assert "couldn't verify" in reply
        assert len(repo.orders) == 0


def test_commit_then_exception_verified_from_order_record():
    svc, repo, ident, chat, gate, executor = setup(crash_after_commit=True)
    run(svc.handle_turn(ident, "grilled pork"))
    reply = run(svc.handle_turn(ident, "yes"))
    assert "order is placed" in reply
    assert len(repo.orders) == 1


def test_unrecognized_gateway_result_fails_closed():
    svc, repo, ident, chat, gate, executor = setup(decision="block")
    run(svc.handle_turn(ident, "grilled pork"))
    reply = run(svc.handle_turn(ident, "yes"))
    assert "Nothing has been placed" in reply
    assert executor.calls == []


def test_model_generated_unverified_success_is_suppressed():
    svc, repo, ident, chat, gate, executor = setup()
    chat.responses = [ChatTurn(text="Your order is placed and confirmed!")]
    reply = run(svc.handle_turn(ident, "Tell me when my order is ready."))
    assert "haven't placed" in reply
    assert repo.orders == {}


def test_bad_tool_response_fails_without_gateway_call():
    svc, repo, ident, chat, gate, executor = setup()
    chat.responses = [ChatTurn(proposal={**PAYLOAD, "approved": True})]
    reply = run(svc.handle_turn(ident, "grilled pork"))
    assert "couldn't understand" in reply
    assert repo.actions == repo.orders == {}
    assert gate.calls == []


def test_unavailable_chat_has_safe_fallback():
    svc, repo, ident, chat, gate, executor = setup()

    class FailChat:
        async def generate(self, turns):
            raise BookingChatError("unavailable")

    svc.chat = FailChat()
    reply = run(svc.handle_turn(ident, "grilled pork"))
    assert "couldn't understand" in reply
    assert executor.calls == []


def test_unknown_interaction_is_not_created_from_caller_supplied_id():
    svc, repo, ident, chat, gate, executor = setup()
    with pytest.raises(ValueError, match="Unknown"):
        run(svc.handle_turn(uuid4(), "Order something"))


@pytest.mark.parametrize("spoken", [
    "Yes.", "yeah", "Yep!", "Yes, that's correct.", "yes please", "That's right", "Okay", "Sounds good, thanks",
])
def test_spoken_confirmations_place_once(spoken):
    svc, repo, ident, chat, gate, executor = setup()
    run(svc.handle_turn(ident, "Grilled pork please"))
    assert "order is placed" in run(svc.handle_turn(ident, spoken))
    assert len(repo.orders) == len(gate.calls) == len(executor.calls) == 1


@pytest.mark.parametrize("spoken", [
    "yes but make it four sandwiches", "yeah, actually change the drink", "okay wait", "yes, not the pork though",
    "right, no", "that's not correct", "maybe", "yesterday I called",
    "yes, and add an iced coffee", "yeah plus another pork one", "sure, also a coffee", "yes without the coffee",
])
def test_changed_or_unclear_replies_never_confirm(spoken):
    svc, repo, ident, chat, gate, executor = setup()
    run(svc.handle_turn(ident, "Grilled pork please"))
    run(svc.handle_turn(ident, spoken))
    assert repo.actions == repo.orders == {}
    assert gate.calls == executor.calls == []


@pytest.mark.parametrize("sent,stored,spoken", [
    (["grilled_pork"], ["grilled_pork"], "1 Grilled pork banh mi, $8.50 total, under Sam."),
    (["Grilled pork banh mi", "Vietnamese Iced Coffee"], ["grilled_pork", "iced_coffee"],
     "1 Grilled pork banh mi and 1 Vietnamese iced coffee, $13.00 total, under Sam."),
    (["party-tray", "Party_Tray", "iced coffee"], ["party_tray", "party_tray", "iced_coffee"],
     "2 Party tray and 1 Vietnamese iced coffee, $124.50 total, under Sam."),
    # Unknown: left as said for the gate to refuse, and no total is read out.
    (["pizza", "grilled_pork"], ["pizza", "grilled_pork"], "1 pizza and 1 Grilled pork banh mi, under Sam."),
])
def test_items_are_stored_as_codes_and_read_back_by_name_with_server_total(sent, stored, spoken):
    svc, repo, ident, *_ = setup(payload={**PAYLOAD, "items": sent})
    reply = run(svc.handle_turn(ident, "Order please"))
    assert reply == spoken + " Is that correct?"
    assert repo.pending[ident]["items"] == stored


def test_placed_reply_states_the_stored_total():
    svc, repo, ident, *_ = setup()
    run(svc.handle_turn(ident, "One grilled pork for Sam"))
    assert run(svc.handle_turn(ident, "yes")) == "Your order is placed. The total is $8.50. Thank you!"


@pytest.mark.parametrize("spoken", [
    "Okay, two of those and a cold cut special for Ravi.",
    "Sure, I'll have a grilled pork banh mi for Sam",
    "Yes I would like to order a party tray please",
])
def test_an_order_that_opens_with_a_yes_word_reaches_the_model_when_nothing_is_pending(spoken):
    svc, repo, ident, chat, gate, executor = setup()
    reply = run(svc.handle_turn(ident, spoken))
    assert "Is that correct?" in reply and len(chat.calls) == 1
    assert ident in repo.pending and repo.actions == {}


@pytest.mark.parametrize("spoken", ["yes", "Okay.", "Yes, that's correct."])
def test_a_bare_yes_with_nothing_pending_never_calls_the_model_or_gate(spoken):
    svc, repo, ident, chat, gate, executor = setup()
    assert "waiting for confirmation" in run(svc.handle_turn(ident, spoken))
    assert chat.calls == gate.calls == executor.calls == []
