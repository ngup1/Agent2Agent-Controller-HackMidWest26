"""SSA-014 adapter over SSA-001's real PostgreSQL TranscriptRepository.

All methods are scoped to the server-configured demo business. Pending booking
read-back state is persisted in 005_ssa014.sql (never process memory). One
confirmation is claimed and an action inserted in the same database transaction.
"""
from __future__ import annotations

import re
from contextlib import nullcontext
from uuid import UUID

from psycopg.types.json import Jsonb

from backend.database.repository import TranscriptRepository, _connect
from backend.domain.booking_proposal import parse_booking_arguments


class PostgresConversationRepository:
    def __init__(self, business_id: UUID, connection_factory=_connect):
        self.business_id = business_id
        self.connection_factory = connection_factory
        self.base = TranscriptRepository(connection_factory=connection_factory)

    def _check_business(self, business_id: UUID) -> None:
        if UUID(str(business_id)) != self.business_id:
            raise ValueError("Unknown business")

    def create_interaction(
        self,
        business_id: UUID,
        channel: str = "api",
        external_reference: str | None = None,
        callback_number: str | None = None,
    ) -> UUID:
        self._check_business(business_id)
        return self.base.create_interaction(
            self.business_id,
            channel=channel,
            external_reference=external_reference,
            callback_number=callback_number,
        )

    def get_interaction(self, interaction_id: UUID) -> dict | None:
        with self.connection_factory() as conn:
            row = conn.execute(
                """SELECT id, business_id, channel, callback_number
                   FROM interactions WHERE id=%s AND business_id=%s""",
                (interaction_id, self.business_id),
            ).fetchone()
        if row is None:
            return None
        return dict(zip(("id", "business_id", "channel", "callback_number"), row))

    def save_transcript_segment(self, interaction_id: UUID, speaker: str, text: str) -> int:
        return self.base.save_segment(
            self.business_id, interaction_id, text, embedding=None, speaker=speaker
        )

    def get_recent_turns(self, interaction_id: UUID, limit: int = 12) -> list[dict]:
        if not 1 <= limit <= 12:
            raise ValueError("Recent turn limit must be 1-12")
        with self.connection_factory() as conn:
            rows = conn.execute(
                """SELECT s.speaker, s.content FROM transcript_segments s
                   JOIN interactions i ON i.id=s.interaction_id
                   WHERE i.id=%s AND i.business_id=%s
                     AND s.speaker IN ('caller', 'business_agent')
                   ORDER BY s.sequence_number DESC LIMIT %s""",
                (interaction_id, self.business_id, limit),
            ).fetchall()
        return [
            {"role": "user" if speaker == "caller" else "assistant", "content": content}
            for speaker, content in reversed(rows)
        ]

    def get_pending_proposal(self, interaction_id: UUID) -> dict | None:
        with self.connection_factory() as conn:
            row = conn.execute(
                """SELECT p.payload FROM pending_booking_proposals p
                   JOIN interactions i ON i.id=p.interaction_id
                   WHERE p.interaction_id=%s AND p.business_id=%s
                     AND i.business_id=%s AND p.status='pending'""",
                (interaction_id, self.business_id, self.business_id),
            ).fetchone()
        return row[0] if row else None

    def store_pending_proposal(self, interaction_id: UUID, payload: dict) -> None:
        validated = parse_booking_arguments(payload).action_payload()
        with self.connection_factory() as conn:
            with conn.cursor() as cur:
                # Serialize proposed-action changes by locking the parent call.
                cur.execute(
                    "SELECT id FROM interactions WHERE id=%s AND business_id=%s FOR UPDATE",
                    (interaction_id, self.business_id),
                )
                if cur.fetchone() is None:
                    raise ValueError("Unknown interaction")
                cur.execute(
                    """INSERT INTO pending_booking_proposals
                       (interaction_id, business_id, payload, status)
                       VALUES (%s, %s, %s, 'pending')
                       ON CONFLICT (interaction_id) DO UPDATE SET
                          payload=EXCLUDED.payload,
                          status='pending', action_id=NULL,
                          confirmed_at=NULL, created_at=now()""",
                    (interaction_id, self.business_id, Jsonb(validated)),
                )

    def clear_pending_proposal(self, interaction_id: UUID) -> None:
        with self.connection_factory() as conn:
            conn.execute(
                """UPDATE pending_booking_proposals SET status='cleared'
                   WHERE interaction_id=%s AND business_id=%s AND status='pending'""",
                (interaction_id, self.business_id),
            )

    def set_callback_number(self, interaction_id: UUID, number: str) -> None:
        # Called only when a customer explicitly supplies a parsed number;
        # otherwise keep Twilio's caller-ID fallback already on interactions.
        # Ensure no mistaken authorizations from contact info.
        if not isinstance(number, str) or not number.strip() or len(number) > 32:
            raise ValueError("Invalid callback number")
        if not re.fullmatch(r"\+?[0-9() .-]{7,31}", number) or not 7 <= sum(c.isdigit() for c in number) <= 15:
            raise ValueError("Invalid callback number")
        with self.connection_factory() as conn:
            updated = conn.execute(
                """UPDATE interactions SET callback_number=%s
                   WHERE id=%s AND business_id=%s RETURNING id""",
                (number, interaction_id, self.business_id),
            ).fetchone()
        if updated is None:
            raise ValueError("Unknown interaction")

    def confirm_pending_proposal(self, interaction_id: UUID) -> UUID | None:
        """A second confirmed 'yes' cannot insert a second action for this proposal."""
        with self.connection_factory() as conn:
            with conn.cursor() as cur:
                # Lock parent before child, matching store_pending_proposal.
                cur.execute(
                    "SELECT id, callback_number FROM interactions "
                    "WHERE id=%s AND business_id=%s FOR UPDATE",
                    (interaction_id, self.business_id),
                )
                interaction = cur.fetchone()
                if interaction is None:
                    raise ValueError("Unknown interaction")
                cur.execute(
                    """SELECT payload, status FROM pending_booking_proposals
                       WHERE interaction_id=%s AND business_id=%s FOR UPDATE""",
                    (interaction_id, self.business_id),
                )
                row = cur.fetchone()
                if row is None or row[1] != "pending":
                    return None
                payload = parse_booking_arguments(row[0]).action_payload()
                if not payload.get("callback_number") and interaction[1]:
                    fallback = dict(payload, callback_number=interaction[1])
                    try:
                        payload = parse_booking_arguments(fallback).action_payload()
                    except ValueError:
                        pass  # Bad unverified caller ID is not usable contact data.
                # Reuse SSA-001's canonical action-creation helper within the SAME
                # transaction; do not create the action before customer confirmation.
                transaction_repo = TranscriptRepository(connection_factory=lambda: nullcontext(conn))
                action_id = transaction_repo.create_action(self.business_id, interaction_id, payload)
                cur.execute(
                    """UPDATE pending_booking_proposals
                       SET status='confirmed', action_id=%s, confirmed_at=now()
                       WHERE interaction_id=%s AND business_id=%s""",
                    (action_id, interaction_id, self.business_id),
                )
                return action_id

    def get_booking_for_action(self, action_id: UUID) -> dict | None:
        with self.connection_factory() as conn:
            row = conn.execute(
                """SELECT id, action_id, slot_id, created_at FROM bookings
                   WHERE action_id=%s AND business_id=%s""",
                (action_id, self.business_id),
            ).fetchone()
        if row is None:
            return None
        return dict(zip(("id", "action_id", "slot_id", "created_at"), row))

    def resolve_service(self, spoken: str) -> dict | None:
        """Match what the model sent to an active service by code or display name."""
        def key(value: str) -> str:
            return " ".join(re.sub(r"[\s_-]+", " ", value.casefold()).split())

        with self.connection_factory() as conn:
            rows = conn.execute(
                "SELECT code, name FROM services WHERE business_id=%s AND active=TRUE ORDER BY code",
                (self.business_id,),
            ).fetchall()
        for code, name in rows:
            if key(spoken) in {key(code), key(name)}:
                return {"code": code, "name": name}
        return None

    def get_public_business_facts(self) -> str:
        """Build prompt facts from actual seeded services, not model guesses."""
        with self.connection_factory() as conn:
            rows = conn.execute(
                """SELECT code, name, duration_minutes, price_usd
                   FROM services WHERE business_id=%s AND active=TRUE ORDER BY code""",
                (self.business_id,),
            ).fetchall()
        if not rows:
            raise RuntimeError("Demo Salon services are not seeded")
        services = ", ".join(
            f"{name} (code {code}, {minutes} minutes, ${price:.2f})"
            for code, name, minutes, price in rows
        )
        return (
            "Demo Salon, America/Chicago. Seeded appointment slots run 9:00 AM to "
            "5:00 PM local time; only real available slots may be booked. "
            f"Published services: {services}."
        )
