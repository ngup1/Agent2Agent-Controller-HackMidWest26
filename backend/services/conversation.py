"""SSA-014: one text-turn service for POST /chat, browser microphone and Twilio.

The repository, gateway, chat model and executor are injected. THIS MODULE DOES
NOT create bookings itself, trust model authorization fields or substitute fakes
when a live provider fails. See docs/ssa-014-integration.md for repo contract.
"""
from __future__ import annotations

import inspect
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from backend.domain.booking_proposal import BookingProposal, parse_booking_arguments
from backend.services.booking_chat import BookingChatError

CALLBACK_LINE = (
    "I've passed this to our staff. Someone will call you back shortly "
    "to confirm. Nothing is booked yet."
)
CONFIRM_WORDS = {"yes", "yes please", "confirm", "correct", "that's correct", "that is correct"}
CANCEL_WORDS = {"no", "no thanks", "cancel", "never mind", "nevermind"}


def _normalized_yes_no(text: str) -> str:
    # Only accept a complete affirmative, not "yes but actually change it".
    return re.sub(r"[.!?]+$", "", text.strip().casefold()).strip()


def _display_time(value: datetime, timezone: str) -> str:
    local = value.astimezone(ZoneInfo(timezone))
    time_text = local.strftime("%I:%M %p").lstrip("0")
    return f"{local.strftime('%A, %B')} {local.day} at {time_text}"


def _recap(proposal: BookingProposal, timezone: str) -> str:
    people = "person" if proposal.party_size == 1 else "people"
    return (
        f"A {proposal.service} for {proposal.party_size} {people} "
        f"on {_display_time(proposal.start_time, timezone)}, "
        f"under {proposal.customer_name}. Is that correct?"
    )


def _safe_nontransactional_text(text: str) -> str:
    # Not a general response safety filter (deliberately outside demo scope).
    # This narrow check enforces the demo's "no fake booking-success" rule.
    clean = " ".join(text.split())[:300]
    if not clean:
        return "How can I help with an appointment?"
    if re.search(r"\b(your|you(?:'re| are))\b.{0,100}\b(booked|confirmed|reserved|scheduled)\b", clean, re.I):
        return "I haven't booked anything yet. What appointment would you like?"
    return clean


def _clarification_for(decision: Any) -> str:
    reasons = getattr(decision, "reasons", None)
    if isinstance(decision, dict):
        reasons = decision.get("reasons", [])
    codes = {r.get("code") for r in (reasons or []) if isinstance(r, dict)}
    if "UNKNOWN_SERVICE" in codes:
        return "We don't offer that service. Could you choose another?"
    if "NO_CAPACITY" in codes or "SLOT_UNAVAILABLE" in codes:
        return "That appointment time isn't available. Could you pick another?"
    if "PARTY_TOO_LARGE" in codes:
        return "That group is too large for a booking. Could you choose a smaller group?"
    return "I can't book that request as asked. Could you choose another service or time?"


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
        pending = self.repository.get_pending_proposal(interaction_id)

        if pending is not None:
            pending_proposal = parse_booking_arguments(pending)
            normalized = _normalized_yes_no(text)
            if normalized in CONFIRM_WORDS:
                reply = await self._confirm(interaction_id)
            elif normalized in CANCEL_WORDS:
                self.repository.clear_pending_proposal(interaction_id)
                reply = "Okay, nothing is booked. What would you like instead?"
            else:
                # Don't accidentally book the old proposal after a correction.
                self.repository.clear_pending_proposal(interaction_id)
                reply = (
                    "I haven't booked that. Please repeat the service, time, "
                    "party size, and name you want."
                )
        elif _normalized_yes_no(text) in CONFIRM_WORDS:
            reply = "I don't have an appointment waiting for confirmation. What would you like to book?"
        else:
            try:
                turns = self.repository.get_recent_turns(interaction_id, limit=12)
                model_turn = await _await_if_needed(self.chat.generate(turns))
                if model_turn.proposal is not None:
                    proposal = parse_booking_arguments(
                        model_turn.proposal.action_payload()
                        if isinstance(model_turn.proposal, BookingProposal)
                        else model_turn.proposal
                    )
                    self.repository.store_pending_proposal(interaction_id, proposal.action_payload())
                    if proposal.callback_number:
                        self.repository.set_callback_number(interaction_id, proposal.callback_number)
                    reply = _recap(proposal, self.business_timezone)
                else:
                    reply = _safe_nontransactional_text(model_turn.text)
            except (BookingChatError, ValueError) as exc:
                # A bad tool JSON / unavailable model does not call the gate.
                reply = (
                    "I couldn't understand that request right now. "
                    "Please say the service, time, party size, and name again."
                )

        self.repository.save_transcript_segment(interaction_id, "business_agent", reply)
        return reply

    async def _confirm(self, interaction_id: Any) -> str:
        # Atomic PostgreSQL claim + action insert is REQUIRED from SSA-001.
        # A duplicate yes cannot insert another action from the same proposal.
        action_id = self.repository.confirm_pending_proposal(interaction_id)
        if action_id is None:
            return "That request was already processed. Please start a new booking if needed."
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
            return "I couldn't authorize that appointment. Nothing has been booked."

        try:
            await _await_if_needed(self.executor.execute(action_id))
        except Exception:
            # A partially completed write must not be described as failed/succeeded.
            pass
        # Check committed booking evidence, NOT arbitrary model/gate/executor text.
        booking = self.repository.get_booking_for_action(action_id)
        if booking is not None:
            return "Your appointment is booked. We look forward to seeing you!"
        return "I couldn't verify that booking. Please ask staff to check before trying again."


# Expose the spec's stable name to voice adapters when using an initialized service:
#     reply = await conversation_service.handle_turn(interaction_id, text)
