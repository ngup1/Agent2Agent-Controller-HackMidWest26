"""SSA-014: one text-turn service for POST /chat, browser microphone and Twilio.

The repository, gateway, chat model and executor are injected. THIS MODULE DOES
NOT place orders itself, trust model authorization fields or substitute fakes
when a live provider fails. See docs/ssa-014-integration.md for repo contract.
"""
from __future__ import annotations

import inspect
import re
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from backend.domain.order_proposal import OrderProposal, parse_order_arguments
from backend.services.booking_chat import BookingChatError

CALLBACK_LINE = (
    "I've passed this to our staff. Someone will call you back shortly "
    "to confirm. Your order is not placed yet."
)
# A spoken yes varies ("yeah", "yes, that's correct"), so match how it starts.
AFFIRMATIVE = re.compile(
    r"^(yes|yeah|yep|yup|sure|ok|okay|correct|right|confirm|absolutely|go ahead|sounds good|"
    r"that'?s (right|correct)|that is (right|correct))\b"
)
# Any of these means the caller is changing the request, not confirming it.
# Additions count: "yes, and add a coffee" must not place the order as read back.
CHANGE_WORDS = re.compile(
    r"\b(but|actually|instead|change|wait|except|no|not|don'?t|"
    r"add|also|plus|another|extra|more|remove|without)\b"
)
CANCEL_WORDS = {"no", "no thanks", "cancel", "never mind", "nevermind"}


def _normalized_yes_no(text: str) -> str:
    # Only accept a complete affirmative, not "yes but actually change it".
    return re.sub(r"[.!?]+$", "", text.strip().casefold()).strip()


def _is_confirmation(text: str) -> bool:
    clean = " ".join(re.sub(r"[^\w' ]+", " ", text.casefold()).split())
    return bool(AFFIRMATIVE.match(clean)) and not CHANGE_WORDS.search(clean)


def _is_bare_confirmation(text: str) -> bool:
    # "Okay, two of those and a coffee" opens with a yes-word but is an order.
    return _is_confirmation(text) and len(text.split()) <= 4


def _item_key(value: str) -> str:
    return " ".join(re.sub(r"[\s_-]+", " ", value.casefold()).split())


def _resolve_items(items: list[str], menu: dict) -> list[str]:
    """Map what the model sent to menu codes, by code or display name.

    An item that is not on the menu is left as said, so the gate answers
    that it is not offered.
    """
    by_key = {}
    for code, item in menu.items():
        by_key[_item_key(code)] = code
        by_key[_item_key(item["name"])] = code
    return [by_key.get(_item_key(item), item) for item in items]


def _recap(proposal: OrderProposal, menu: dict) -> str:
    """Read the order back. The total is the server's, from current menu prices."""
    counts: dict[str, int] = {}
    for item in proposal.items:
        counts[item] = counts.get(item, 0) + 1
    parts = [f"{count} {menu[code]['name'] if code in menu else code}" for code, count in counts.items()]
    listing = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    if all(code in menu for code in counts):
        total = sum((menu[code]["price"] for code in proposal.items), Decimal("0"))
        listing += f", ${total:.2f} total"
    return f"{listing}, under {proposal.customer_name}. Is that correct?"


def _safe_nontransactional_text(text: str) -> str:
    # Not a general response safety filter (deliberately outside demo scope).
    # This narrow check enforces the demo's "no fake order-success" rule.
    clean = " ".join(text.split())[:300]
    if not clean:
        return "What can I get for you?"
    if re.search(r"\b(your|you(?:'re| are))\b.{0,100}\b(placed|booked|confirmed|reserved|scheduled)\b", clean, re.I):
        return "I haven't placed any order yet. What would you like?"
    return clean


def _clarification_for(decision: Any) -> str:
    reasons = getattr(decision, "reasons", None)
    if isinstance(decision, dict):
        reasons = decision.get("reasons", [])
    codes = {r.get("code") for r in (reasons or []) if isinstance(r, dict)}
    if "UNKNOWN_ITEM" in codes:
        return "One of those items isn't on our menu. What would you like instead?"
    if "EMPTY_ORDER" in codes:
        return "I don't have any items for that order. What would you like?"
    return "I can't place that order as asked. What would you like instead?"


async def _await_if_needed(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class ConversationService:
    """Coordinates proposal -> deterministic recap -> later yes -> gate -> execution.

    The SSA-001 repository must persist pending proposals and claim confirmations
    atomically. In-memory state belongs ONLY in explicitly fake test repositories.
    """

    def __init__(
        self,
        *,
        repository: Any,
        chat: Any,
        gateway: Any,
        executor: Any,
        business_timezone: str = "America/Chicago",
    ) -> None:
        self.repository = repository
        self.chat = chat
        self.gateway = gateway
        self.executor = executor
        self.business_timezone = business_timezone
        ZoneInfo(business_timezone)  # fail on invalid configured timezone

    async def handle_turn(self, interaction_id: Any, text: str) -> str:
        """Called for one completed customer utterance; returns speech-ready text."""
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError("Customer turn must contain 1-4000 characters")
        # Missing/unknown interactions must fail; never create arbitrary business IDs.
        if self.repository.get_interaction(interaction_id) is None:
            raise ValueError("Unknown interaction")
        self.repository.save_transcript_segment(interaction_id, "caller", text)
        phone_match = re.search(r"(\+1\d{10})", text)
        if phone_match:
            try:
                self.repository.set_callback_number(interaction_id, phone_match.group(1))
            except Exception:
                pass
        pending = self.repository.get_pending_proposal(interaction_id)

        if pending is not None:
            parse_order_arguments(pending)  # a stored proposal must still be valid
            normalized = _normalized_yes_no(text)
            if _is_confirmation(text):
                reply = await self._confirm(interaction_id)
            elif normalized in CANCEL_WORDS:
                self.repository.clear_pending_proposal(interaction_id)
                reply = "Okay, nothing is ordered. What would you like instead?"
            else:
                # Don't accidentally place the old order after a correction.
                self.repository.clear_pending_proposal(interaction_id)
                reply = (
                    "I haven't placed that. Please tell me the full order "
                    "and the name again."
                )
        elif _is_bare_confirmation(text):
            reply = "I don't have an order waiting for confirmation. What would you like?"
        else:
            try:
                turns = self.repository.get_recent_turns(interaction_id, limit=12)
                model_turn = await _await_if_needed(self.chat.generate(turns))
                if model_turn.proposal is not None:
                    proposal = parse_order_arguments(
                        model_turn.proposal.action_payload()
                        if isinstance(model_turn.proposal, OrderProposal)
                        else model_turn.proposal
                    )
                    # The gate matches menu codes exactly, and the model may send
                    # display names.
                    menu = self.repository.get_menu()
                    proposal = proposal.model_copy(
                        update={"items": _resolve_items(proposal.items, menu)}
                    )
                    self.repository.store_pending_proposal(interaction_id, proposal.action_payload())
                    if proposal.callback_number:
                        self.repository.set_callback_number(interaction_id, proposal.callback_number)
                    reply = _recap(proposal, menu)
                else:
                    reply = _safe_nontransactional_text(model_turn.text)
            except (BookingChatError, ValueError) as exc:
                # A bad tool JSON / unavailable model does not call the gate.
                reply = (
                    "I couldn't understand that request right now. "
                    "Please tell me what you'd like to order and your name again."
                )

        self.repository.save_transcript_segment(interaction_id, "business_agent", reply)
        return reply

    async def _confirm(self, interaction_id: Any) -> str:
        # Atomic PostgreSQL claim + action insert is REQUIRED from SSA-001.
        # A duplicate yes cannot insert another action from the same proposal.
        action_id = self.repository.confirm_pending_proposal(interaction_id)
        if action_id is None:
            return "That request was already processed. Please start a new order if needed."
        try:
            result = await _await_if_needed(self.gateway.evaluate(action_id))
        except Exception:
            return "I couldn't check that request. Nothing is confirmed yet. Please ask staff for help."

        decision = result.get("decision") if isinstance(result, dict) else getattr(result, "decision", None)
        if decision == "escalate":
            return CALLBACK_LINE
        if decision == "clarify":
            return _clarification_for(result)
        if decision != "allow":
            return "I couldn't authorize that order. Nothing has been placed."

        try:
            await _await_if_needed(self.executor.execute(action_id))
        except Exception:
            # A partially completed write must not be described as failed/succeeded.
            pass
        # Check committed order evidence, NOT arbitrary model/gate/executor text.
        order = self.repository.get_order_for_action(action_id)
        if order is not None:
            return f"Your order is placed. The total is ${order['total_usd']:.2f}. Thank you!"
        return "I couldn't verify that order. Please ask staff to check before trying again."


# Expose the spec's stable name to voice adapters when using an initialized service:
#     reply = await conversation_service.handle_turn(interaction_id, text)
