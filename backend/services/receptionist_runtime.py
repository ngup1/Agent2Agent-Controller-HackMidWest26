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


class GateAdapter:
    """The SSA-010 gate, run off the event loop with the app's own settings."""

    def __init__(self, settings, connection_factory=None):
        self.settings = settings
        self.connection_factory = connection_factory

    async def evaluate(self, action_id):
        from backend.database.gate_store import GateStore
        from backend.services import gateway
        store = GateStore(self.connection_factory) if self.connection_factory else None
        return await asyncio.to_thread(gateway.evaluate, action_id, settings=self.settings, store=store)


class BookingExecutor:
    """The SSA-005 calendar. It refuses any action the gate or staff did not clear."""

    def __init__(self, settings, connection_factory=None):
        self.settings = settings
        self.connection_factory = connection_factory

    async def execute(self, action_id):
        from backend.connectors import booking
        return await asyncio.to_thread(
            booking.execute, action_id, self.settings.business_id, self.connection_factory
        )


class UnavailableGateway:
    """Explicit missing dependency: NEVER an automatic allow."""

    async def evaluate(self, action_id):
        raise RuntimeError("SSA-010 gateway is not connected")


class UnavailableExecutor:
    """Explicit missing dependency: NEVER a simulated booking."""

    def execute(self, action_id):
        raise RuntimeError("SSA-005 booking executor is not connected")
