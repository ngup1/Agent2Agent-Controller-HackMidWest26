"""SSA-001 FastAPI entry point, with SSA-014 /chat enabled when configured.

The gate and booking executor are injected by their owning lanes. They never
fall back to permissive fakes in the deployed application.
"""

import psycopg
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from backend.config import Settings


def create_app(
    settings: Settings | None = None,
    *,
    repository=None,
    chat=None,
    gateway=None,
    executor=None,
) -> FastAPI:
    app = FastAPI(title="Supervised Service Agent")
    app.state.settings = settings or Settings.from_env()

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
    if not settings.chat_session_secret or len(settings.chat_session_secret) < 24:
        @app.post("/chat")
        def chat_not_configured():
            return JSONResponse(
                status_code=503,
                content={"detail": "Chat session configuration is unavailable"},
            )
        return app

    from backend.api.chat import create_chat_router
    from backend.database.conversation_repository import PostgresConversationRepository
    from backend.services.conversation import ConversationService
    from backend.services.receptionist_runtime import (
        DatabaseFactsBookingChat, UnavailableExecutor, UnavailableGateway,
    )

    repo = repository if repository is not None else PostgresConversationRepository(settings.business_id)
    chat_ready = True
    if chat is None:
        if not settings.openai_api_key:
            chat_ready = False
        else:
            from openai import OpenAI
            from backend.providers.akamai import AkamaiService
            # Own retries in BookingChat: avoid OpenAI SDK retry amplification.
            client = OpenAI(
                base_url=settings.openai_base_url,
                api_key=settings.openai_api_key,
                timeout=12.0,
                max_retries=0,
            )
            chat = DatabaseFactsBookingChat(
                AkamaiService(client=client, chat_model=settings.chat_model),
                repo,
            )
    service = ConversationService(
        repository=repo,
        chat=chat,
        gateway=gateway if gateway is not None else UnavailableGateway(),
        executor=executor if executor is not None else UnavailableExecutor(),
    )
    app.state.conversation_service = service
    app.state.conversation_repository = repo
    app.include_router(create_chat_router(
        service=service,
        repository=repo,
        business_id=settings.business_id,
        secret=settings.chat_session_secret,
        enabled=chat_ready,
    ))
    return app


app = create_app()
