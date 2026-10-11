"""Replay readback/idempotence with real PostgreSQL and an explicitly fake app."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import sql

from backend.config import DEMO_BUSINESS_ID, Settings
from backend.database.replay import ReplayRepository
from backend.memory.precedents import PrecedentService
from backend.scripts.replay_calls import HttpReplayTransport, RecordedCall, replay_call
from tests.integration.test_foundation import database, PROPOSAL

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("outcome", ["approved", "denied"])
def test_replay_twice_preserves_one_call_action_embedding_and_decision(database, outcome):
    fake_app = FastAPI()
    counts = {"turn": 0, "review": 0}

    @fake_app.post("/chat")
    def chat(body: dict):
        counts["turn"] += 1
        interaction = body["interaction_id"]
        database.repo.save_segment(DEMO_BUSINESS_ID, interaction, body["text"])
        if body["text"] == "Yes":
            action = database.repo.create_action(DEMO_BUSINESS_ID, interaction, PROPOSAL)
            PrecedentService(database.repo, Settings(embedding_mode="fake")).retrieve_for_action(action)
            database.conn.execute("UPDATE agent_actions SET state='pending_review' WHERE id=%s", (action,))
            database.conn.execute("INSERT INTO human_reviews (action_id) VALUES (%s)", (action,))
        return {"reply": "Explicit fake conversation"}

    @fake_app.post("/reviews/{review_id}/{decision}")
    def decide(review_id: str, decision: str):
        counts["review"] += 1
        status = "approved" if decision == "approve" else "denied"
        action = database.conn.execute(
            "UPDATE human_reviews SET status=%s, decided_at=now() WHERE id=%s RETURNING action_id",
            (status, review_id),
        ).fetchone()[0]
        state = "executed" if status == "approved" else "rejected"
        database.conn.execute("UPDATE agent_actions SET state=%s WHERE id=%s", (state, action))
        if status == "approved":
            slot = database.conn.execute("SELECT id FROM slots LIMIT 1").fetchone()[0]
            database.conn.execute(
                "INSERT INTO bookings (business_id, action_id, slot_id, party_size, customer_name) VALUES (%s, %s, %s, 1, 'Test')",
                (DEMO_BUSINESS_ID, action, slot),
            )
        return {"status": status}

    recording = RecordedCall("integration-test", "manipulation", ("Synthetic override request", "Yes"), outcome,
                             ({"speaker": "caller", "content": "Synthetic override request"},
                              {"speaker": "caller", "content": "Yes"}))
    repository = ReplayRepository(database.connect)
    with TestClient(fake_app) as client:
        transport = HttpReplayTransport(client, "/reviews/{review_id}/{decision}")
        assert replay_call(recording, DEMO_BUSINESS_ID, repository, transport) == "replayed"
        assert replay_call(recording, DEMO_BUSINESS_ID, repository, transport) == "skipped"
    assert counts == {"turn": 2, "review": 1}
    for table in ("interactions", "agent_actions", "transcript_embeddings", "human_reviews"):
        assert database.conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()[0] == 1
    assert database.conn.execute("SELECT count(*) FROM bookings").fetchone()[0] == (1 if outcome == "approved" else 0)


def test_export_reads_voice_transcript_and_actual_staff_outcome(database):
    interaction = database.repo.create_interaction(DEMO_BUSINESS_ID, "web")
    database.repo.save_segment(DEMO_BUSINESS_ID, interaction, "Synthetic voice request")
    database.repo.save_segment(DEMO_BUSINESS_ID, interaction, "Synthetic reply", speaker="business_agent")
    action = database.repo.create_action(DEMO_BUSINESS_ID, interaction, PROPOSAL)
    repository = ReplayRepository(database.connect)
    with pytest.raises(ValueError, match="Finish"):
        repository.recording(DEMO_BUSINESS_ID, interaction)
    database.conn.execute("UPDATE agent_actions SET state='rejected' WHERE id=%s", (action,))
    database.conn.execute("INSERT INTO human_reviews (action_id, status, note) VALUES (%s, 'denied', 'Synthetic test note')", (action,))
    result = repository.recording(DEMO_BUSINESS_ID, interaction)
    assert result["outcome"] == "denied"
    assert result["review_note"] == "Synthetic test note"
    assert [s["speaker"] for s in result["transcript"]] == ["caller", "business_agent"]
    api_interaction = database.repo.create_interaction(DEMO_BUSINESS_ID, "api")
    with pytest.raises(ValueError, match="browser or phone"):
        repository.recording(DEMO_BUSINESS_ID, api_interaction)
