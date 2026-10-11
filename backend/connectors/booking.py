"""In-process booking connector for slots and bookings tables."""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

from backend.config import DEMO_BUSINESS_ID, Settings
from backend.database.repository import _connect


class BookingExecutionError(Exception):
    pass


class ActionNotExecutableError(BookingExecutionError):
    pass


class SlotCapacityError(BookingExecutionError):
    pass


def _get_connection(connection_factory=None):
    if connection_factory:
        return connection_factory()
    return _connect()


def list_slots(
    service: str,
    day: date | datetime | str,
    business_id: UUID = DEMO_BUSINESS_ID,
    connection_factory=None,
) -> list[dict[str, Any]]:
    """List open slots for a service on a given day with remaining capacity."""
    if isinstance(day, str):
        target_date = date.fromisoformat(day)
    elif isinstance(day, datetime):
        target_date = day.date()
    else:
        target_date = day

    tz = ZoneInfo("America/Chicago")
    day_start = datetime.combine(target_date, time.min, tzinfo=tz).astimezone(timezone.utc)
    day_end = datetime.combine(target_date, time.max, tzinfo=tz).astimezone(timezone.utc)

    query = """
        SELECT
            s.id AS slot_id,
            s.start_time,
            s.end_time,
            s.capacity,
            s.capacity - COALESCE(SUM(b.party_size), 0) AS remaining_capacity
        FROM slots s
        JOIN services srv ON s.service_id = srv.id
        LEFT JOIN bookings b ON b.slot_id = s.id
        WHERE s.business_id = %s
          AND srv.code = %s
          AND s.closed = FALSE
          AND s.start_time >= %s
          AND s.start_time <= %s
        GROUP BY s.id, s.start_time, s.end_time, s.capacity
        ORDER BY s.start_time ASC;
    """

    with _get_connection(connection_factory) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, (business_id, service, day_start, day_end))
            return [dict(row) for row in cur.fetchall()]


def execute(
    action_id: UUID | str,
    business_id: UUID = DEMO_BUSINESS_ID,
    connection_factory=None,
) -> dict[str, Any]:
    """Execute an allowed or approved action. Atomically check capacity and book."""
    action_id = UUID(str(action_id))

    with _get_connection(connection_factory) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            # 1. Check idempotency: already executed?
            cur.execute(
                """SELECT b.id AS booking_id, b.slot_id, b.party_size, b.customer_name,
                          b.callback_number, a.state
                   FROM agent_actions a
                   LEFT JOIN bookings b ON b.action_id = a.id
                   WHERE a.id = %s AND a.business_id = %s""",
                (action_id, business_id),
            )
            action_row = cur.fetchone()
            if not action_row:
                raise ActionNotExecutableError(f"Action {action_id} not found")

            if action_row["state"] == "executed" and action_row["booking_id"]:
                return {
                    "status": "executed",
                    "booking_id": action_row["booking_id"],
                    "action_id": action_id,
                    "slot_id": action_row["slot_id"],
                    "customer_name": action_row["customer_name"],
                    "party_size": action_row["party_size"],
                    "already_existed": True,
                }

            # 2. Check execution gate: state allowed or approved review
            cur.execute(
                """SELECT a.state, a.payload, hr.status AS review_status
                   FROM agent_actions a
                   LEFT JOIN human_reviews hr ON hr.action_id = a.id
                   WHERE a.id = %s AND a.business_id = %s""",
                (action_id, business_id),
            )
            state_info = cur.fetchone()
            state = state_info["state"]
            review_status = state_info["review_status"]
            payload = state_info["payload"]

            is_executable = (
                state == "allowed"
                or (state == "pending_review" and review_status == "approved")
            )
            if not is_executable:
                raise ActionNotExecutableError(
                    f"Action {action_id} cannot be executed in state='{state}' (review='{review_status}')"
                )

        # 3. Atomic transaction: lock slot row, check capacity, write booking
        with conn.transaction():
            with conn.cursor(row_factory=dict_row) as cur:
                service_code = payload.get("service")
                party_size = int(payload.get("party_size", 1))
                customer_name = payload.get("customer_name")
                callback_number = payload.get("callback_number")
                start_time_iso = payload.get("start_time")
                start_time = datetime.fromisoformat(start_time_iso)

                # Locate slot
                cur.execute(
                    """SELECT s.id, s.capacity, s.closed
                       FROM slots s
                       JOIN services srv ON s.service_id = srv.id
                       WHERE s.business_id = %s
                         AND srv.code = %s
                         AND s.start_time = %s
                       FOR UPDATE OF s""",
                    (business_id, service_code, start_time),
                )
                slot = cur.fetchone()
                if not slot:
                    cur.execute("UPDATE agent_actions SET state='failed' WHERE id=%s", (action_id,))
                    return {"status": "failed", "reason": "SLOT_NOT_FOUND", "action_id": action_id}

                if slot["closed"]:
                    cur.execute("UPDATE agent_actions SET state='failed' WHERE id=%s", (action_id,))
                    return {"status": "failed", "reason": "SLOT_CLOSED", "action_id": action_id}

                # Compute booked capacity
                cur.execute(
                    "SELECT COALESCE(SUM(party_size), 0) AS booked FROM bookings WHERE slot_id = %s",
                    (slot["id"],),
                )
                booked = cur.fetchone()["booked"]

                if booked + party_size > slot["capacity"]:
                    cur.execute("UPDATE agent_actions SET state='failed' WHERE id=%s", (action_id,))
                    return {
                        "status": "failed",
                        "reason": "SLOT_FULL",
                        "action_id": action_id,
                        "available_capacity": max(0, slot["capacity"] - booked),
                    }

                # Insert booking & transition action state
                cur.execute(
                    """INSERT INTO bookings (business_id, action_id, slot_id, party_size, customer_name, callback_number)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (action_id) DO NOTHING
                       RETURNING id""",
                    (business_id, action_id, slot["id"], party_size, customer_name, callback_number),
                )
                booking_res = cur.fetchone()
                booking_id = booking_res["id"] if booking_res else None

                cur.execute("UPDATE agent_actions SET state='executed' WHERE id=%s", (action_id,))

                return {
                    "status": "executed",
                    "booking_id": booking_id,
                    "action_id": action_id,
                    "slot_id": slot["id"],
                    "customer_name": customer_name,
                    "party_size": party_size,
                    "already_existed": False,
                }


book = execute
