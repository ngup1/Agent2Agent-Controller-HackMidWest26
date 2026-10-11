from unittest.mock import patch

import psycopg
from fastapi.testclient import TestClient

from backend.config import DEMO_BUSINESS_ID, Settings
from backend.main import create_app


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
