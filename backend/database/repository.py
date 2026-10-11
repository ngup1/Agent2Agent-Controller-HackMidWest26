"""Business-scoped transcript + vector store using Akamai Managed PostgreSQL.

Requires psycopg 3 and pgvector (run migrations/002_governance.sql first).
No DDL is executed on request and caller input never supplies business identity.
"""
from __future__ import annotations

import os
from collections.abc import Sequence
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from backend.config import Settings


def _connect():
    import psycopg
    from pgvector.psycopg import register_vector
    conn = psycopg.connect(Settings.from_env().database_url, connect_timeout=10)
    try:
        register_vector(conn)
    except Exception:
        conn.close()
        raise
    return conn


class TranscriptRepository:
    def __init__(self, connection_factory=_connect):
        self.connection_factory = connection_factory

    def create_demo_business(self, name: str = "Demo Restaurant") -> UUID:
        """Local smoke-test helper; DO NOT expose to public callers."""
        with self.connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO businesses(name, industry) VALUES (%s, %s) RETURNING id", (name, "restaurant"))
                business_id = cur.fetchone()[0]
                cur.execute("INSERT INTO business_policies(business_id) VALUES (%s)", (business_id,))
                return business_id

    def create_interaction(self, business_id: UUID, channel: str = "api",
                           external_reference: str | None = None,
                           callback_number: str | None = None) -> UUID:
        if channel not in {"phone", "api", "a2a", "web"}:
            raise ValueError("Invalid channel")
        with self.connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO interactions
                       (business_id, channel, external_reference, callback_number)
                       VALUES (%s, %s, %s, %s) RETURNING id""",
                    (business_id, channel, external_reference, callback_number),
                )
                return cur.fetchone()[0]

    def save_segment(self, business_id: UUID, interaction_id: UUID, transcript: str,
                     embedding: Sequence[float] | None = None,
                     speaker: str = "caller") -> int:
        """Persist text even when embeddings are unavailable; optionally save both."""
        if speaker not in {"caller", "business_agent", "human", "unknown"}:
            raise ValueError("Invalid speaker")
        if embedding is not None and len(embedding) != 1024:
            raise ValueError("Embedding must contain 1024 dimensions")
        with self.connection_factory() as conn:
            with conn.cursor() as cur:
                # Only allow inserting into an interaction owned by business_id.
                cur.execute(
                    "SELECT id FROM interactions WHERE id=%s AND business_id=%s FOR UPDATE",
                    (interaction_id, business_id),
                )
                if cur.fetchone() is None:
                    raise ValueError("Unknown interaction for this business")
                # Lock serializes sequence generation for multiple concurrent call segments.
                cur.execute(
                    "SELECT COALESCE(MAX(sequence_number), -1) + 1 FROM transcript_segments WHERE interaction_id=%s",
                    (interaction_id,),
                )
                seq = cur.fetchone()[0]
                cur.execute(
                    """INSERT INTO transcript_segments(interaction_id, sequence_number, speaker, content)
                       VALUES (%s,%s,%s,%s) RETURNING id""",
                    (interaction_id, seq, speaker, transcript),
                )
                segment_id = cur.fetchone()[0]
                if embedding is not None:
                    cur.execute(
                        """INSERT INTO transcript_embeddings
                           (transcript_segment_id, embedding_model, embedding)
                           VALUES (%s,%s,%s::vector)""",
                        (segment_id, os.getenv("EMBEDDING_MODEL", "hackathon-embedding"), list(embedding)),
                    )
                return segment_id

    def attach_embedding(self, business_id: UUID, segment_id: int,
                         embedding: Sequence[float],
                         embedding_model: str | None = None) -> bool:
        """Attach once after text persistence. Returns False if already attached."""
        if len(embedding) != 1024:
            raise ValueError("Embedding must contain 1024 dimensions")
        with self.connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT s.id FROM transcript_segments s
                       JOIN interactions i ON i.id=s.interaction_id
                       WHERE s.id=%s AND i.business_id=%s""",
                    (segment_id, business_id),
                )
                if cur.fetchone() is None:
                    raise ValueError("Unknown segment for this business")
                cur.execute(
                    """INSERT INTO transcript_embeddings
                       (transcript_segment_id, embedding_model, embedding)
                       VALUES (%s,%s,%s::vector)
                       ON CONFLICT (transcript_segment_id) DO NOTHING
                       RETURNING transcript_segment_id""",
                    (segment_id, embedding_model or os.getenv("EMBEDDING_MODEL", "hackathon-embedding"), list(embedding)),
                )
                return cur.fetchone() is not None

    def create_action(self, business_id: UUID, interaction_id: UUID,
                      payload: dict, action_type: str = "book_appointment") -> UUID:
        """Record a proposal only. The gateway owns authorization and state changes."""
        if action_type != "book_appointment":
            raise ValueError("Only book_appointment proposals are supported by the demo")
        if not isinstance(payload, dict):
            raise ValueError("Action payload must be an object")
        with self.connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM interactions WHERE id=%s AND business_id=%s",
                    (interaction_id, business_id),
                )
                if cur.fetchone() is None:
                    raise ValueError("Unknown interaction for this business")
                cur.execute(
                    """INSERT INTO agent_actions
                       (business_id, interaction_id, action_type, payload, idempotency_key)
                       VALUES (%s,%s,%s,%s,%s) RETURNING id""",
                    (business_id, interaction_id, action_type, Jsonb(payload), str(uuid4())),
                )
                return cur.fetchone()[0]

    def record_decision(self, business_id: UUID, action_id: UUID,
                        decision: str, risk_level: str, reasons: list[dict],
                        policy_snapshot: dict) -> int:
        """Save gate evidence without granting execution authority."""
        if decision not in {"allow", "escalate", "clarify"}:
            raise ValueError("Invalid demo decision")
        if risk_level not in {"low", "medium", "high", "unknown"}:
            raise ValueError("Invalid risk level")
        if not isinstance(reasons, list) or not isinstance(policy_snapshot, dict):
            raise ValueError("Reasons must be a list and policy_snapshot an object")
        with self.connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT interaction_id FROM agent_actions WHERE id=%s AND business_id=%s",
                    (action_id, business_id),
                )
                row = cur.fetchone()
                if row is None:
                    raise ValueError("Unknown action for this business")
                cur.execute(
                    """INSERT INTO security_decisions
                       (interaction_id, action_id, decision, risk_level, reasons, policy_snapshot)
                       VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (row[0], action_id, decision, risk_level, Jsonb(reasons), Jsonb(policy_snapshot)),
                )
                return cur.fetchone()[0]

    def get_segment(self, business_id: UUID, segment_id: int) -> dict | None:
        with self.connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT s.id, s.content, s.speaker, s.interaction_id
                       FROM transcript_segments s
                       JOIN interactions i ON s.interaction_id=i.id
                       WHERE s.id=%s AND i.business_id=%s""",
                    (segment_id, business_id),
                )
                row = cur.fetchone()
        if row is None:
            return None
        return {"segment_id": row[0], "transcript": row[1], "speaker": row[2], "interaction_id": str(row[3])}

    def search_similar(self, business_id: UUID, embedding: Sequence[float], limit: int = 3) -> list[dict]:
        if len(embedding) != 1024:
            raise ValueError("Embedding must contain 1024 dimensions")
        if not 1 <= limit <= 20:
            raise ValueError("limit must be 1-20")
        with self.connection_factory() as conn:
            with conn.cursor() as cur:
                # Filtering by business_id prevents cross-business transcript search.
                cur.execute(
                    """SELECT s.id, s.content, e.embedding <=> %s::vector AS distance
                       FROM transcript_embeddings e
                       JOIN transcript_segments s ON s.id=e.transcript_segment_id
                       JOIN interactions i ON i.id=s.interaction_id
                       WHERE i.business_id=%s
                       ORDER BY distance ASC LIMIT %s""",
                    (list(embedding), business_id, limit),
                )
                rows = cur.fetchall()
        return [{"segment_id": r[0], "transcript": r[1], "distance": float(r[2])} for r in rows]
