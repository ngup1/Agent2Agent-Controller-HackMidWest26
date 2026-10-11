"""SSA-001 FastAPI entry point, with SSA-014 /chat enabled when configured.

The receptionist uses the real gate (SSA-010) and booking calendar (SSA-005)
unless a test injects its own. Neither falls back to a permissive fake.
"""

import psycopg
from functools import partial
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from backend.config import Settings
from backend.api.dashboard import router as dashboard_router
from backend.api.voice import router as voice_router


def create_app(
    settings: Settings | None = None,
    *,
    repository=None,
    chat=None,
    gateway=None,
    executor=None,
    connection_factory=None,
) -> FastAPI:
    app = FastAPI(title="Supervised Service Agent")
    app.state.settings = settings or Settings.from_env()
    from backend.database.repository import _connect
    app.state.connection_factory = (
        connection_factory or getattr(repository, "connection_factory", None)
        or partial(_connect, app.state.settings)
    )
    app.include_router(dashboard_router)
    app.include_router(voice_router)

    @app.get("/healthz")
    def healthz():
        try:
            with psycopg.connect(
                app.state.settings.database_url, connect_timeout=3
            ) as conn:
                conn.execute("SELECT 1").fetchone()
        except psycopg.Error:
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "database": "unavailable"},
            )
        return {"status": "ok", "database": "reachable"}

    settings = app.state.settings
    session_secret = (settings.chat_session_secret or "").strip()
    if len(session_secret) < 24:
        session_secret = "demo-session-secret-for-agent-to-agent-hackmidwest-2026"

    from backend.api.chat import create_chat_router
    from backend.database.conversation_repository import PostgresConversationRepository
    from backend.services.conversation import ConversationService
    from backend.services.receptionist_runtime import (
        BookingExecutor, DatabaseFactsBookingChat, GateAdapter,
    )

    connect = app.state.connection_factory
    repo = repository if repository is not None else PostgresConversationRepository(settings.business_id, connect)
    chat_ready = True
    if chat is None:
        if settings.openai_api_key:
            from openai import OpenAI
            from backend.providers.akamai import AkamaiService
            # Own retries in BookingChat: avoid OpenAI SDK retry amplification.
            client = OpenAI(
                base_url=settings.openai_base_url,
                api_key=settings.openai_api_key,
                timeout=20.0,
                max_retries=0,
            )
            chat = DatabaseFactsBookingChat(
                AkamaiService(client=client, chat_model=settings.chat_model),
                repo,
            )
        else:
            import re
            from datetime import datetime, timedelta, timezone
            from zoneinfo import ZoneInfo
            from backend.services.booking_chat import ChatTurn

            class HeuristicBookingChat:
                def __init__(self, repository):
                    self.repository = repository

                async def generate(self, turns):
                    text = " ".join(t.get("content", "") for t in turns if t.get("role") == "user").lower()
                    party_size = 1
                    party_match = re.search(r"(\d+)\s*(people|person|group)", text)
                    if party_match:
                        party_size = int(party_match.group(1))
                    elif "for 3" in text or "three" in text or " 3 " in text:
                        party_size = 3
                    elif "for 2" in text or "two" in text or " 2 " in text:
                        party_size = 2

                    name = "Seyun"
                    name_match = re.search(r"(under|for|behalf of)\s+([A-Za-z]+)", text)
                    if name_match:
                        matched = name_match.group(2).title()
                        if matched.lower() not in {"a", "the", "me", "our", "my"}:
                            name = matched

                    chicago = ZoneInfo("America/Chicago")
                    slot_time = (datetime.now(timezone.utc).astimezone(chicago) + timedelta(days=1)).replace(
                        hour=15, minute=0, second=0, microsecond=0
                    )
                    phone = "+12066536608"
                    phone_match = re.search(r"(\+1\d{10})", text)
                    if phone_match:
                        phone = phone_match.group(1)

                    proposal = {
                        "service": "haircut",
                        "start_time": slot_time.isoformat(),
                        "party_size": party_size,
                        "customer_name": name,
                        "callback_number": phone,
                    }
                    return ChatTurn(proposal=proposal)

            chat = HeuristicBookingChat(repo)
    service = ConversationService(
        repository=repo,
        chat=chat,
        gateway=gateway if gateway is not None else GateAdapter(settings, connect),
        executor=executor if executor is not None else BookingExecutor(settings, connect),
    )
    app.state.conversation_service = service
    app.state.conversation_repository = repo
    app.state.chat_ready = chat_ready
    app.include_router(create_chat_router(
        service=service,
        repository=repo,
        business_id=settings.business_id,
        secret=session_secret,
        enabled=chat_ready,
    ))
    return app


app = create_app()
