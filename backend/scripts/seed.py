"""Repeatable synthetic Demo Salon seed. No calls, decisions or bookings are seeded."""

import argparse
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from backend.config import Settings

BOOKING_RULES = {
    "timezone": "America/Chicago",
    "booking_horizon_days": 14,
    "slot_minutes": 30,
    "default_slot_capacity": 3,
    "max_party_size_auto": 1,
    "max_party_size_hard": 3,
    "min_notice_minutes_auto": 120,
    "min_notice_minutes_hard": 30,
    "allowed_exception_codes": ["GROUP_SIZE", "SHORT_NOTICE"],
    "approval_ttl_seconds": 300,
    "automation_paused": False,
}
SERVICES = (
    ("haircut", "Haircut", "30.00"),
    ("beard_trim", "Beard trim", "15.00"),
)


def future_slots(now: datetime, days: int = 3):
    """Half-hour slots, 09:00–17:00 Chicago time, strictly after the given clock."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Seed clock must include a timezone")
    if not 1 <= days <= BOOKING_RULES["booking_horizon_days"]:
        raise ValueError("Seed days must be between 1 and 14")
    local_zone = ZoneInfo(BOOKING_RULES["timezone"])
    today = now.astimezone(local_zone).date()
    for offset in range(days):
        opening = datetime.combine(today + timedelta(days=offset), time(9), local_zone)
        for half_hour in range(16):
            start = opening + timedelta(minutes=30 * half_hour)
            if start > now:
                yield start.astimezone(timezone.utc), (start + timedelta(minutes=30)).astimezone(timezone.utc)


def seed(conn, business_id, now: datetime | None = None, days: int = 3):
    slots = list(future_slots(now or datetime.now(timezone.utc), days))
    with conn.transaction():
        conn.execute(
            """INSERT INTO businesses (id, name, industry) VALUES (%s, 'Demo Salon', 'salon')
               ON CONFLICT (id) DO NOTHING""",
            (business_id,),
        )
        conn.execute(
            """INSERT INTO business_policies (business_id, booking_rules) VALUES (%s, %s)
               ON CONFLICT (business_id) DO UPDATE SET booking_rules=EXCLUDED.booking_rules
               WHERE business_policies.booking_rules='{}'::jsonb""",
            (business_id, Jsonb(BOOKING_RULES)),
        )
        for code, name, price in SERVICES:
            conn.execute(
                """INSERT INTO services (business_id, code, name, duration_minutes, price_usd)
                   VALUES (%s, %s, %s, 30, %s)
                   ON CONFLICT (business_id, code) DO NOTHING""",
                (business_id, code, name, price),
            )
            service_id = conn.execute(
                "SELECT id FROM services WHERE business_id=%s AND code=%s",
                (business_id, code),
            ).fetchone()[0]
            for start, end in slots:
                conn.execute(
                    """INSERT INTO slots (business_id, service_id, start_time, end_time, capacity)
                       VALUES (%s, %s, %s, %s, %s)
                       ON CONFLICT (service_id, start_time) DO NOTHING""",
                    (business_id, service_id, start, end, BOOKING_RULES["default_slot_capacity"]),
                )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--now", type=datetime.fromisoformat, help="Timezone-aware ISO clock for replay")
    args = parser.parse_args()
    settings = Settings.from_env()
    with psycopg.connect(settings.database_url, connect_timeout=10) as conn:
        seed(conn, settings.business_id, args.now, args.days)
    print("Seeded Demo Salon, two services and future slots")


if __name__ == "__main__":
    main()
