from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import psycopg
import pytest
from fastapi.testclient import TestClient

from backend.config import DEMO_BUSINESS_ID, Settings
from backend.main import create_app
from backend.scripts.seed import future_slots


def test_health_failure_hides_database_credentials():
    settings = Settings(database_url="postgresql://demo:secret@invalid/demo")
    with patch("backend.main.psycopg.connect", side_effect=psycopg.OperationalError("secret")):
        response = TestClient(create_app(settings)).get("/healthz")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "database": "unavailable"}
    assert "secret" not in response.text
    assert "secret" not in repr(settings)


def test_server_environment_selects_business_and_preserves_workshop_names(monkeypatch):
    business_id = "343a43e8-f52e-4ec7-9825-fecbfe27f6a3"
    monkeypatch.setenv("DEMO_BUSINESS_ID", business_id)
    monkeypatch.setenv("DATABASE_URL", "postgresql://akmadmin:test@host/defaultdb?sslmode=require")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://ws.akamai-workshops.com/v1")
    monkeypatch.setenv("CHAT_MODEL", "hackathon-chat")
    monkeypatch.setenv("EMBEDDING_MODEL", "hackathon-embedding")
    settings = Settings.from_env()
    assert str(settings.business_id) == business_id
    assert settings.database_url.endswith("?sslmode=require")
    assert settings.chat_model == "hackathon-chat"
    assert settings.embedding_model == "hackathon-embedding"
    assert create_app(settings).state.settings.business_id == settings.business_id
    assert Settings().business_id == DEMO_BUSINESS_ID


@pytest.mark.parametrize("clock", ["2026-10-10T08:00:00-05:00", "2026-11-01T08:00:00-06:00"])
def test_seed_clock_generates_future_utc_slots_in_business_hours(clock):
    now = datetime.fromisoformat(clock)
    slots = list(future_slots(now, 3))
    assert len(slots) == 48
    assert len(set(slots)) == len(slots)
    assert all(start > now and end - start == timedelta(minutes=30) for start, end in slots)
    assert all(start.tzinfo == timezone.utc for start, _ in slots)
    assert slots[0][0] == now.astimezone(timezone.utc) + timedelta(hours=1)


@pytest.mark.parametrize("clock,days", [(datetime(2026, 10, 10), 3), (datetime.now(timezone.utc), 15)])
def test_seed_rejects_ambiguous_clock_and_excess_horizon(clock, days):
    with pytest.raises(ValueError):
        list(future_slots(clock, days))
