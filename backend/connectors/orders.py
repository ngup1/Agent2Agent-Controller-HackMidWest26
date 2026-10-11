"""In-process order connector for the orders table (SSA-031)."""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.config import DEMO_BUSINESS_ID
from backend.database.repository import _connect


class OrderExecutionError(Exception):
    pass


class ActionNotExecutableError(OrderExecutionError):
    pass


def _get_connection(connection_factory=None):
    if connection_factory:
        return connection_factory()
    return _connect()


def execute(
    action_id: UUID | str,
    business_id: UUID = DEMO_BUSINESS_ID,
    connection_factory=None,
) -> dict[str, Any]:
    """Place an allowed or staff-approved order, priced from the menu, exactly once."""
    action_id = UUID(str(action_id))

    with _get_connection(connection_factory) as conn:
        with conn.transaction():
            with conn.cursor(row_factory=dict_row) as cur:
                # The row lock serializes two executions of the same action.
                cur.execute(
                    """SELECT a.state, a.action_type, a.payload, hr.status AS review_status,
                              o.id AS order_id, o.total_usd
                       FROM agent_actions a
                       LEFT JOIN human_reviews hr ON hr.action_id = a.id
                       LEFT JOIN orders o ON o.action_id = a.id
                       WHERE a.id = %s AND a.business_id = %s
                       FOR UPDATE OF a""",
                    (action_id, business_id),
                )
                action = cur.fetchone()
                if not action:
                    raise ActionNotExecutableError(f"Action {action_id} not found")
                if action["state"] == "executed" and action["order_id"]:
                    return {"status": "executed", "order_id": action["order_id"], "action_id": action_id,
                            "total_usd": action["total_usd"], "already_existed": True}

                state, review_status = action["state"], action["review_status"]
                is_executable = action["action_type"] == "place_order" and (
                    state == "allowed"
                    or (state == "pending_review" and review_status == "approved")
                )
                if not is_executable:
                    raise ActionNotExecutableError(
                        f"Action {action_id} cannot be executed in state='{state}' (review='{review_status}')"
                    )

                payload = action["payload"]
                codes = payload.get("items")
                cur.execute(
                    "SELECT code, name, price_usd FROM services WHERE business_id=%s AND active",
                    (business_id,),
                )
                menu = {row["code"]: row for row in cur.fetchall()}
                if not isinstance(codes, list) or not codes or any(code not in menu for code in codes):
                    # The menu changed after the gate decided, or the payload is unusable.
                    cur.execute("UPDATE agent_actions SET state='failed' WHERE id=%s", (action_id,))
                    return {"status": "failed", "reason": "UNKNOWN_ITEM", "action_id": action_id}

                items = [{"code": code, "name": menu[code]["name"], "price": f"{menu[code]['price_usd']:.2f}"}
                         for code in codes]
                total = sum((menu[code]["price_usd"] for code in codes), Decimal("0"))
                cur.execute(
                    """INSERT INTO orders (business_id, action_id, items, total_usd, customer_name, callback_number)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (action_id) DO NOTHING
                       RETURNING id""",
                    (business_id, action_id, Jsonb(items), total,
                     payload.get("customer_name"), payload.get("callback_number")),
                )
                order = cur.fetchone()
                cur.execute("UPDATE agent_actions SET state='executed' WHERE id=%s", (action_id,))
                return {"status": "executed", "order_id": order["id"] if order else None,
                        "action_id": action_id, "total_usd": total, "already_existed": False}
