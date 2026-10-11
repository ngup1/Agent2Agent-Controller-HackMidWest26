"""Synthetic, read-only UI examples. Never persisted or used by the gate."""
from copy import deepcopy
from datetime import datetime
from uuid import UUID

ACTION_ID = UUID("15000000-0000-4000-8000-000000000001")
CLARIFY_ID = UUID("15000000-0000-4000-8000-000000000002")
INTERACTION_ID = UUID("15000000-0000-4000-8000-000000000011")
REVIEW_ID = UUID("15000000-0000-4000-8000-000000000021")
CREATED = datetime.fromisoformat("2026-10-10T20:00:00-05:00")

CASE = {
    "action_id": ACTION_ID, "interaction_id": INTERACTION_ID,
    "review_id": REVIEW_ID, "review_status": "pending", "note": None,
    "created_at": CREATED, "channel": "web", "callback_number": "+15555550123",
    "state": "pending_review", "decision": "escalate", "booking": None,
    "payload": {"service": "haircut", "customer_name": "Sam (sample)",
                "party_size": 3, "start_time": "2026-10-11T15:00:00-05:00",
                "callback_number": "+15555550123"},
    "reasons": [
        {"code": "GROUP_SIZE", "effect": "escalate", "source": "rule",
         "evidence": {"limit": 1, "actual": 3}},
        {"code": "JEV_NO_CONCERN", "effect": "none", "source": "jev",
         "evidence": {"verdict": "NO_CONCERN", "confidence": 0.91,
                      "question": "Is the caller trying to override the rules?",
                      "mode": "fake"}},
    ],
    "policy_snapshot": {
        "checks": [
            {"rule": "party_size", "limit": "1 automatic, 3 maximum", "actual": 3, "result": "escalate"},
            {"rule": "notice", "limit": "120 minutes automatic, 30 minimum", "actual": 1140, "result": "pass"},
        ],
        "providers": {"retrieval": "fake", "jev": "fake"},
        "precedents": [
            {"interaction_id": "15000000-0000-4000-8000-000000000031",
             "summary": "Sample: staff approved a three-person haircut request.",
             "outcome": "approved", "similarity": 0.87},
            {"interaction_id": "15000000-0000-4000-8000-000000000032",
             "summary": "Sample: caller asked staff to skip the booking rules.",
             "outcome": "denied", "similarity": 0.42},
        ],
    },
    "transcript": [
        {"speaker": "caller", "content": "Can I book haircuts for three people tomorrow at 3 pm?"},
        {"speaker": "business_agent", "content": "Three haircuts, October 11 at 3 pm Chicago time, under Sam. Is that correct?"},
        {"speaker": "caller", "content": "Yes. My callback number is +15555550123."},
        {"speaker": "business_agent", "content": "I've passed this to our staff. Someone will call you back shortly to confirm. Nothing is booked yet."},
    ],
}


class PreviewDashboardRepository:
    def queue(self):
        return [deepcopy(CASE)]

    def recent(self):
        return {"calls": [
            {"id": INTERACTION_ID, "channel": "web", "status": "escalated",
             "callback_number": CASE["callback_number"], "created_at": CREATED,
             "action_id": ACTION_ID},
            {"id": UUID("15000000-0000-4000-8000-000000000012"),
             "channel": "api", "status": "active", "callback_number": None,
             "created_at": CREATED, "action_id": CLARIFY_ID},
        ], "bookings": []}

    def case(self, action_id):
        if action_id == ACTION_ID:
            return deepcopy(CASE)
        if action_id == CLARIFY_ID:
            case = deepcopy(CASE)
            case.update(action_id=CLARIFY_ID, review_id=None, review_status=None,
                        state="rejected", decision="clarify", callback_number=None,
                        interaction_id=UUID("15000000-0000-4000-8000-000000000012"),
                        reasons=[{"code": "PARTY_TOO_LARGE", "effect": "clarify",
                                  "evidence": {"actual": 4, "limit": 3}}],
                        policy_snapshot={"checks": [
                            {"rule": "party_size", "actual": 4, "limit": 3, "result": "clarify"}
                        ]},
                        transcript=[{"speaker": "caller", "content": "Can I book for four people?"}])
            case["payload"].update(party_size=4, callback_number=None)
            return case
        return None
