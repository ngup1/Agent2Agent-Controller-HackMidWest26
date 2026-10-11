"""Explicit SSA-014 fake dependencies. Never load these in the cloud/live app."""
from __future__ import annotations

from uuid import uuid4

from backend.services.booking_chat import ChatTurn


class FakeRepository:
    def __init__(self):
        self.interactions = {}
        self.transcripts = {}
        self.pending = {}
        self.actions = {}
        self.bookings = {}
        self.claims = 0

    def create_interaction(self, business_id, channel="api", callback_number=""):
        interaction_id = uuid4()
        self.interactions[interaction_id] = {
            "business_id": business_id, "channel": channel, "callback_number": callback_number
        }
        self.transcripts[interaction_id] = []
        return interaction_id

    def get_interaction(self, interaction_id):
        return self.interactions.get(interaction_id)

    def save_transcript_segment(self, interaction_id, speaker, text):
        self.transcripts[interaction_id].append({"role": "user" if speaker == "caller" else "assistant", "content": text})

    def get_recent_turns(self, interaction_id, limit=12):
        return self.transcripts[interaction_id][-limit:]

    def get_pending_proposal(self, interaction_id):
        return self.pending.get(interaction_id)

    def store_pending_proposal(self, interaction_id, payload):
        self.pending[interaction_id] = payload

    def clear_pending_proposal(self, interaction_id):
        self.pending.pop(interaction_id, None)

    def set_callback_number(self, interaction_id, number):
        self.interactions[interaction_id]["callback_number"] = number

    def confirm_pending_proposal(self, interaction_id):
        # One fake atomic pop. Actual repository MUST use a real PostgreSQL transaction.
        payload = self.pending.pop(interaction_id, None)
        if payload is None:
            return None
        action_id = uuid4()
        self.actions[action_id] = {"interaction_id": interaction_id, "payload": payload, "state": "proposed"}
        self.claims += 1
        return action_id

    def get_booking_for_action(self, action_id):
        return self.bookings.get(action_id)


class FakeChat:
    def __init__(self, *responses: ChatTurn):
        self.responses = list(responses)
        self.calls = []

    async def generate(self, turns):
        self.calls.append(turns)
        return self.responses.pop(0)


class FakeGateway:
    def __init__(self, decision="allow", reasons=None):
        self.decision = decision
        self.reasons = reasons or []
        self.calls = []

    async def evaluate(self, action_id):
        self.calls.append(action_id)
        return {"decision": self.decision, "reasons": self.reasons}


class FakeExecutor:
    def __init__(self, repository, create_booking=True, crash_after_commit=False):
        self.repository = repository
        self.create_booking = create_booking
        self.crash_after_commit = crash_after_commit
        self.calls = []

    def execute(self, action_id):
        self.calls.append(action_id)
        if self.create_booking:
            self.repository.bookings.setdefault(action_id, {"id": str(uuid4()), "action_id": action_id})
        if self.crash_after_commit:
            raise TimeoutError("response lost after fake commit")
