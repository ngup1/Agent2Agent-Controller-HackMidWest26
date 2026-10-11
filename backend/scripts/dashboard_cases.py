"""Create or inspect two synthetic local dashboard cases; no provider calls."""
import argparse
import re
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from backend.config import Settings


def create_cases(conn, business_id, batch):
    # Serialize repeated invocations for this business. Existing cases are never reset.
    if conn.execute("SELECT id FROM businesses WHERE id=%s FOR UPDATE", (business_id,)).fetchone() is None:
        raise ValueError("Seed the business first: python -m backend.scripts.seed")
    for label in ("approve", "deny"):
        key = f"ssa015-manual:{batch}:{label}"
        if conn.execute(
            "SELECT id FROM agent_actions WHERE business_id=%s AND idempotency_key=%s",
            (business_id, key),
        ).fetchone():
            continue
        interaction_id, action_id = uuid4(), uuid4()
        name = f"SYNTHETIC - {label.upper()} - {batch}"
        payload = {"items": ["party_tray", "party_tray"], "customer_name": name,
                   "callback_number": "+15555550123"}
        conn.execute("""
            INSERT INTO interactions(id,business_id,channel,status,external_reference,callback_number)
            VALUES (%s,%s,'api','escalated',%s,%s)
        """, (interaction_id, business_id, key, payload["callback_number"]))
        conn.execute("""
            INSERT INTO transcript_segments(interaction_id,sequence_number,speaker,content)
            VALUES (%s,0,'caller',%s)
        """, (interaction_id, "SYNTHETIC MANUAL TEST: Request two party trays."))
        conn.execute("""
            INSERT INTO agent_actions(id,interaction_id,business_id,action_type,payload,idempotency_key,state)
            VALUES (%s,%s,%s,'place_order',%s,%s,'pending_review')
        """, (action_id, interaction_id, business_id, Jsonb(payload), key))
        conn.execute("""
            INSERT INTO security_decisions(interaction_id,action_id,decision,risk_level,reasons,policy_snapshot)
            VALUES (%s,%s,'escalate','medium',%s,%s)
        """, (interaction_id, action_id,
              Jsonb([{"code": "ORDER_OVER_LIMIT", "source": "rule", "effect": "escalate",
                      "evidence": {"limit": "100.00", "actual": "120.00"}}]),
              Jsonb({"synthetic_manual_test": True,
                     "order_total": "120.00",
                     "checks": [{"rule": "order_total", "limit": "$100.00 automatic",
                                 "actual": "$120.00", "result": "escalate"}],
                     "precedents": [], "providers": {"retrieval": "unset", "jev": "unset"}})))
        conn.execute("INSERT INTO human_reviews(action_id) VALUES (%s)", (action_id,))


def case_status(conn, business_id, batch):
    return conn.execute("""
        SELECT a.payload->>'customer_name', r.status, a.state,
               (SELECT count(*) FROM orders o WHERE o.action_id=a.id), a.id
        FROM agent_actions a JOIN human_reviews r ON r.action_id=a.id
        WHERE a.business_id=%s AND a.idempotency_key IN (%s,%s)
        ORDER BY a.idempotency_key
    """, (business_id, f"ssa015-manual:{batch}:approve", f"ssa015-manual:{batch}:deny")).fetchall()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["create", "status"])
    parser.add_argument("--batch", default="local-1")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", args.batch):
        parser.error("Batch must be 1-40 letters, digits, underscores or hyphens.")
    settings = Settings.from_env()
    if conninfo_to_dict(settings.database_url).get("host") not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("This fixture command is for a local database only.")
    try:
        with psycopg.connect(settings.database_url, connect_timeout=5) as conn:
            if args.command == "create":
                create_cases(conn, settings.business_id, args.batch)
            rows = case_status(conn, settings.business_id, args.batch)
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    except psycopg.Error:
        parser.exit(1, "Database operation failed. Check the local connection, migrations and seed.\n")
    print("SYNTHETIC local dashboard tests. These bypass the gate and do not test providers.")
    if not rows:
        print("No cases found. Run the create command for this batch.")
    for name, review, action, orders, action_id in rows:
        print(f"{name}: review={review}, action={action}, orders={orders}")
        print(f"  http://127.0.0.1:8000/dashboard/cases/{action_id}")


if __name__ == "__main__":
    main()
