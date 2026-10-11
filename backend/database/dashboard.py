"""Read-only, business-scoped dashboard queries. No workflow mutations here."""
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from backend.config import Settings


class DashboardRepository:
    def __init__(self, settings: Settings, connection_factory=None):
        self.business_id = settings.business_id
        self.connect = connection_factory or (lambda: psycopg.connect(
            settings.database_url, connect_timeout=3,
            options="-c statement_timeout=5000",
        ))

    def queue(self):
        with self.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT r.id AS review_id, r.status AS review_status, r.created_at,
                       a.id AS action_id, a.payload, a.state, i.channel,
                       d.reasons
                FROM human_reviews r
                JOIN agent_actions a ON a.id=r.action_id
                JOIN interactions i ON i.id=a.interaction_id AND i.business_id=a.business_id
                LEFT JOIN LATERAL (
                    SELECT reasons FROM security_decisions
                    WHERE action_id=a.id AND interaction_id=i.id
                    ORDER BY created_at DESC, id DESC LIMIT 1
                ) d ON true
                WHERE a.business_id=%s
                ORDER BY (r.status='pending') DESC,
                         CASE WHEN r.status='pending' THEN r.created_at END ASC,
                         r.created_at DESC, r.id
                LIMIT 100
            """, (self.business_id,))
            return cur.fetchall()

    def recent(self):
        with self.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT i.id, i.channel, i.status, i.callback_number, i.created_at,
                       a.id AS action_id
                FROM interactions i
                LEFT JOIN LATERAL (
                    SELECT id FROM agent_actions
                    WHERE interaction_id=i.id AND business_id=i.business_id
                    ORDER BY created_at DESC, id DESC LIMIT 1
                ) a ON true
                WHERE i.business_id=%s ORDER BY i.created_at DESC, i.id LIMIT 20
            """, (self.business_id,))
            calls = cur.fetchall()
            cur.execute("""
                SELECT b.id, b.action_id, b.customer_name, b.party_size,
                       s.start_time, v.name AS service
                FROM bookings b
                JOIN slots s ON s.id=b.slot_id AND s.business_id=b.business_id
                JOIN services v ON v.id=s.service_id AND v.business_id=b.business_id
                WHERE b.business_id=%s ORDER BY b.created_at DESC, b.id LIMIT 20
            """, (self.business_id,))
            return {"calls": calls, "bookings": cur.fetchall()}

    def case(self, action_id: UUID):
        with self.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT a.id AS action_id, a.payload, a.state, a.created_at,
                       i.id AS interaction_id, i.channel, i.callback_number,
                       r.id AS review_id, r.status AS review_status, r.note,
                       d.decision, d.reasons, d.policy_snapshot
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
                SELECT b.id, b.customer_name, b.party_size, s.start_time, v.name AS service
                FROM bookings b
                JOIN slots s ON s.id=b.slot_id AND s.business_id=b.business_id
                JOIN services v ON v.id=s.service_id AND v.business_id=b.business_id
                WHERE b.action_id=%s AND b.business_id=%s
            """, (action_id, self.business_id))
            case["booking"] = cur.fetchone()
            return case
