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
    "state": "pending_review", "decision": "escalate", "order": None,
    "payload": {"items": ["party_tray", "party_tray"], "customer_name": "Sam (sample)",
                "callback_number": "+15555550123"},
    "reasons": [
        {"code": "ORDER_OVER_LIMIT", "effect": "escalate", "source": "rule",
         "evidence": {"limit": "100.00", "actual": "120.00"}},
        {"code": "JEV_NO_CONCERN", "effect": "none", "source": "jev",
         "evidence": {"verdict": "NO_CONCERN", "confidence": 0.91,
                      "question": "Is the caller trying to override the rules?",
                      "mode": "fake"}},
    ],
    "policy_snapshot": {
        "order_total": "120.00",
        "checks": [
            {"rule": "menu_items", "limit": "every item is on the menu", "actual": "party_tray, party_tray", "result": "pass"},
            {"rule": "order_total", "limit": "$100.00 automatic", "actual": "$120.00", "result": "escalate"},
            {"rule": "keyword: Allergy mention", "limit": "none of the listed words", "actual": "none", "result": "pass"},
        ],
        "providers": {"retrieval": "fake", "jev": "fake"},
        "precedents": [
            {"interaction_id": "15000000-0000-4000-8000-000000000031",
             "summary": "Sample: staff approved an order of two party trays.",
             "outcome": "approved", "similarity": 0.87},
            {"interaction_id": "15000000-0000-4000-8000-000000000032",
             "summary": "Sample: caller asked staff to skip the order rules.",
             "outcome": "denied", "similarity": 0.42},
        ],
    },
    "transcript": [
        {"speaker": "caller", "content": "Can I get two party trays? The name is Sam."},
        {"speaker": "business_agent", "content": "2 Party tray, $120.00 total, under Sam. Is that correct?"},
        {"speaker": "caller", "content": "Yes."},
        {"speaker": "business_agent", "content": "I've passed this to our staff. Someone will call you back shortly to confirm. Your order is not placed yet."},
    ],
}


class PreviewDashboardRepository:
    def board(self):
        return {"business_name": "Demo Banh Mi (sample)", "rows": [
            {"interaction_id": INTERACTION_ID, "channel": "web", "created_at": CREATED,
             "callback_number": CASE["callback_number"], "recent": False,
             "action_id": ACTION_ID, "payload": deepcopy(CASE["payload"]),
             "state": "pending_review", "review_status": "pending",
             "reasons": deepcopy(CASE["reasons"]), "order_id": None},
            {"interaction_id": UUID("15000000-0000-4000-8000-000000000012"),
             "channel": "phone", "created_at": CREATED, "callback_number": "+15555550199",
             "recent": True, "action_id": None, "payload": None, "state": None,
             "review_status": None, "reasons": None, "order_id": None},
        ]}

    def case(self, action_id):
        if action_id == ACTION_ID:
            return deepcopy(CASE)
        if action_id == CLARIFY_ID:
            case = deepcopy(CASE)
            case.update(action_id=CLARIFY_ID, review_id=None, review_status=None,
                        state="rejected", decision="clarify", callback_number=None,
                        interaction_id=UUID("15000000-0000-4000-8000-000000000012"),
                        reasons=[{"code": "UNKNOWN_ITEM", "effect": "clarify",
                                  "evidence": {"items": ["pizza"]}}],
                        policy_snapshot={"checks": [
                            {"rule": "menu_items", "actual": "pizza", "limit": "every item is on the menu", "result": "clarify"}
                        ]},
                        transcript=[{"speaker": "caller", "content": "Can I get a pizza for Sam?"}])
            case["payload"].update(items=["pizza"], callback_number=None)
            return case
        return None
