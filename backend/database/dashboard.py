"""Read-only, business-scoped dashboard queries. No workflow mutations here."""
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from backend.config import Settings

# A call with no outcome counts as in progress only while it is this recent.
IN_PROGRESS_MINUTES = 10


class DashboardRepository:
    def __init__(self, settings: Settings, connection_factory=None):
        self.business_id = settings.business_id
        self.connect = connection_factory or (lambda: psycopg.connect(
            settings.database_url, connect_timeout=3,
            options="-c statement_timeout=5000",
        ))

    def board(self):
        """The latest 100 calls with the outcome of each call's most recent order proposal."""
        with self.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT name FROM businesses WHERE id=%s", (self.business_id,))
            business = cur.fetchone()
            cur.execute("""
                SELECT i.id AS interaction_id, i.channel, i.callback_number, i.created_at,
                       COALESCE(t.last_at, i.created_at) > now() - make_interval(mins => %s) AS recent,
                       a.id AS action_id, a.payload, a.state,
                       r.status AS review_status, d.reasons,
                       o.id AS order_id, o.items AS order_items, o.total_usd,
                       o.customer_name AS order_name
                FROM interactions i
                LEFT JOIN LATERAL (
                    SELECT max(created_at) AS last_at FROM transcript_segments
                    WHERE interaction_id=i.id
                ) t ON true
                LEFT JOIN LATERAL (
                    SELECT id, payload, state FROM agent_actions
                    WHERE interaction_id=i.id AND business_id=i.business_id
                      AND action_type='place_order'
                    ORDER BY created_at DESC, id DESC LIMIT 1
                ) a ON true
                LEFT JOIN human_reviews r ON r.action_id=a.id
                LEFT JOIN LATERAL (
                    SELECT reasons FROM security_decisions
                    WHERE action_id=a.id AND interaction_id=i.id
                    ORDER BY created_at DESC, id DESC LIMIT 1
                ) d ON true
                LEFT JOIN orders o ON o.action_id=a.id AND o.business_id=i.business_id
                WHERE i.business_id=%s
                ORDER BY i.created_at DESC, i.id LIMIT 100
            """, (IN_PROGRESS_MINUTES, self.business_id))
            return {"business_name": business["name"] if business else None, "rows": cur.fetchall()}

    def case(self, action_id: UUID):
        with self.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT a.id AS action_id, a.payload, a.state, a.created_at,
                       i.id AS interaction_id, i.channel, i.callback_number,
                       r.id AS review_id, r.status AS review_status, r.note,
                       d.decision, d.reasons, d.policy_snapshot,
                       (SELECT name FROM businesses WHERE id=a.business_id) AS business_name
                FROM agent_actions a
                JOIN interactions i ON i.id=a.interaction_id AND i.business_id=a.business_id
                LEFT JOIN human_reviews r ON r.action_id=a.id
                LEFT JOIN LATERAL (
                    SELECT decision, reasons, policy_snapshot FROM security_decisions
                    WHERE action_id=a.id AND interaction_id=i.id
                    ORDER BY created_at DESC, id DESC LIMIT 1
                ) d ON true
                WHERE a.id=%s AND a.business_id=%s
            """, (action_id, self.business_id))
            case = cur.fetchone()
            if case is None:
                return None
            cur.execute("""
                SELECT speaker, content FROM transcript_segments
                WHERE interaction_id=%s ORDER BY sequence_number, id
            """, (case["interaction_id"],))
            case["transcript"] = cur.fetchall()
            cur.execute("""
                SELECT id, customer_name, items, total_usd, created_at
                FROM orders WHERE action_id=%s AND business_id=%s
            """, (action_id, self.business_id))
            case["order"] = cur.fetchone()
            return case
