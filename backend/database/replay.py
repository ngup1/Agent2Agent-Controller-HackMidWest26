"""Recording replay identity and readback; reviews stay owned by the application."""
from backend.database.repository import _connect


class ReplayRepository:
    def __init__(self, connection_factory=_connect):
        self.connection_factory = connection_factory

    def start(self, business_id, interaction_id, reference):
        with self.connection_factory() as conn:
            row = conn.execute(
                """INSERT INTO interactions (id, business_id, channel, external_reference)
                   VALUES (%s, %s, 'api', %s) ON CONFLICT (id) DO NOTHING RETURNING id""",
                (interaction_id, business_id, reference),
            ).fetchone()
        return row is not None

    def state(self, business_id, interaction_id):
        with self.connection_factory() as conn:
            row = conn.execute(
                "SELECT external_reference FROM interactions WHERE id=%s AND business_id=%s",
                (interaction_id, business_id),
            ).fetchone()
            if row is None:
                return None
            turns = conn.execute(
                """SELECT content FROM transcript_segments WHERE interaction_id=%s
                   AND speaker='caller' ORDER BY sequence_number""", (interaction_id,),
            ).fetchall()
            actions = conn.execute(
                """SELECT a.id, a.state, r.id, r.status FROM agent_actions a
                   LEFT JOIN human_reviews r ON r.action_id=a.id
                   WHERE a.interaction_id=%s AND a.business_id=%s ORDER BY a.created_at, a.id""",
                (interaction_id, business_id),
            ).fetchall()
        return {"reference": row[0], "turns": [r[0] for r in turns],
                "actions": [{"id": r[0], "state": r[1], "review_id": r[2], "outcome": r[3]}
                            for r in actions]}

    def recording(self, business_id, interaction_id):
        with self.connection_factory() as conn:
            interaction = conn.execute(
                "SELECT channel FROM interactions WHERE id=%s AND business_id=%s",
                (interaction_id, business_id),
            ).fetchone()
            if interaction is None or interaction[0] not in {"web", "phone"}:
                raise ValueError("Export requires a recorded browser or phone interaction")
            segments = conn.execute(
                """SELECT speaker, content FROM transcript_segments
                   WHERE interaction_id=%s ORDER BY sequence_number""", (interaction_id,),
            ).fetchall()
            actions = conn.execute(
                """SELECT a.state, r.status, r.note FROM agent_actions a
                   LEFT JOIN human_reviews r ON r.action_id=a.id
                   WHERE a.interaction_id=%s AND a.business_id=%s""", (interaction_id, business_id),
            ).fetchall()
        if len(actions) != 1 or not segments:
            raise ValueError("Export requires a transcript and exactly one proposal")
        state, outcome, note = actions[0]
        if outcome not in {None, "approved", "denied"} or state != ("rejected" if outcome == "denied" else "executed"):
            raise ValueError("Finish the call and its review before exporting")
        return {"transcript": [{"speaker": s[0], "content": s[1]} for s in segments],
                "outcome": outcome, "review_note": note}
