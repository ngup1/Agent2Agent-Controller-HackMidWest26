"""Wiring helpers for SSA-014: no fake fallback on live provider failures."""
from __future__ import annotations

import asyncio
from typing import Any

from backend.services.booking_chat import AkamaiBookingChat, BookingChatError


class DatabaseFactsBookingChat:
    """Fetch published salon facts from the same business-scoped database."""

    def __init__(self, akamai_service: Any, repository: Any):
        self.akamai_service = akamai_service
        self.repository = repository

    async def generate(self, turns):
        # Missing DB/seed must not silently return invented service information.
        try:
            facts = await asyncio.to_thread(self.repository.get_public_business_facts)
        except Exception as exc:
            raise BookingChatError("Published salon facts are unavailable") from exc
        return await AkamaiBookingChat(
            self.akamai_service, business_facts=facts
        ).generate(turns)


class UnavailableGateway:
    """Explicit missing dependency: NEVER an automatic allow."""

    async def evaluate(self, action_id):
        raise RuntimeError("SSA-010 gateway is not connected")


class UnavailableExecutor:
    """Explicit missing dependency: NEVER a simulated booking."""

    def execute(self, action_id):
        raise RuntimeError("SSA-005 booking executor is not connected")
