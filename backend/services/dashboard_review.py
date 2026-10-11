"""Staff review orchestration using the order connector."""
from contextlib import nullcontext
from uuid import UUID

from psycopg.rows import dict_row

from backend.connectors.orders import execute
from backend.database.dashboard import DashboardRepository
from backend.services.outbound import OutboundService


class ReviewNotFound(Exception):
    pass


class ReviewConflict(Exception):
    pass


class ReviewService:
    def __init__(self, settings, connection_factory=None, executor=execute, outbound=None):
        self.business_id = settings.business_id
        self.connect = connection_factory or DashboardRepository(settings).connect
        self.executor = executor
        self.outbound = outbound or OutboundService(settings)

    def decide(self, action_id: UUID, decision: str, note: str = ""):
        if decision not in {"approve", "deny"}:
            raise ValueError("Unknown review decision")
        status = "approved" if decision == "approve" else "denied"
        payload = {}
        with self.connect() as conn:
            # Lock the action before the review. The connector shares this transaction:
            # it sees approval before commit, and a crash rolls both writes back.
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT state, payload FROM agent_actions WHERE id=%s AND business_id=%s FOR UPDATE",
                    (action_id, self.business_id),
                )
                action = cur.fetchone()
                if action is None:
                    raise ReviewNotFound()
                payload = action.get("payload") or {}
                cur.execute(
                    "SELECT id, status FROM human_reviews WHERE action_id=%s FOR UPDATE",
                    (action_id,),
                )
                review = cur.fetchone()
                if review is None:
                    raise ReviewNotFound()
                if review["status"] == status:
                    return  # Duplicate of a completed decision: do not execute again.
                if review["status"] != "pending" or action["state"] != "pending_review":
                    raise ReviewConflict()
                cur.execute(
                    """UPDATE human_reviews SET status=%s, reviewer_id='shared-staff',
                       note=%s, decided_at=now() WHERE id=%s""",
                    (status, note.strip() or None, review["id"]),
                )
                if decision == "deny":
                    cur.execute(
                        "UPDATE agent_actions SET state='rejected' WHERE id=%s AND business_id=%s",
                        (action_id, self.business_id),
                    )
                    return
            self.executor(
                action_id, business_id=self.business_id,
                connection_factory=lambda: nullcontext(conn),
            )

        # Trigger outbound voice callback after database commit succeeds
        if decision == "approve" and payload:
            if isinstance(payload, str):
                import json
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}
            callback_number = payload.get("callback_number")
            if callback_number and self.outbound:
                try:
                    self.outbound.call_approved_customer(
                        to_number=callback_number,
                        customer_name=payload.get("customer_name", "Valued Customer"),
                        service=payload.get("service", "appointment"),
                        start_time=payload.get("start_time", "today"),
                    )
                except Exception:
                    pass

