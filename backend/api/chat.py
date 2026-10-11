"""Optional SSA-014 FastAPI router; SSA-001 owns backend/main.py wiring.

POST /chat begins a server-owned 'api' interaction or continues one only with
its HMAC-bound token. Demo-only secret must be supplied by the app (no default).
"""
from __future__ import annotations

import hmac
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from backend.services.conversation_tokens import conversation_token_for


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000)
    interaction_id: UUID | None = None
    conversation_token: str | None = None

    @model_validator(mode="after")
    def require_paired_session(self):
        if (self.interaction_id is None) != (self.conversation_token is None):
            raise ValueError("interaction_id and conversation_token must be supplied together")
        return self


class ChatResponse(BaseModel):
    interaction_id: UUID
    conversation_token: str
    reply: str


def create_chat_router(*, service: Any, repository: Any, business_id: UUID, secret: str, enabled: bool = True) -> APIRouter:
    """Called from SSA-001 main.py when dependencies are ready."""
    if len(secret) < 24:
        raise ValueError("Configure a long server-side CHAT_SESSION_SECRET")

    def token_for(interaction_id: UUID) -> str:
        return conversation_token_for(secret, business_id, interaction_id)

    router = APIRouter()

    @router.post("/chat", response_model=ChatResponse)
    async def chat(request: ChatRequest) -> ChatResponse:
        if not enabled:
            raise HTTPException(503, "Chat model is not configured")
        if request.interaction_id is None:
            try:
                interaction_id = UUID(str(repository.create_interaction(business_id, channel="api")))
            except Exception as exc:
                import logging
                logging.getLogger("uvicorn.error").error("create_interaction failed: %s: %s", type(exc).__name__, exc)
                raise HTTPException(503, f"Database/interaction error: {exc}") from None
        else:
            interaction_id = request.interaction_id
            if not hmac.compare_digest(request.conversation_token or "", token_for(interaction_id)):
                raise HTTPException(403, "Invalid conversation token")
            # Prevent accepting a token for a deleted/changed interaction.
            try:
                interaction = repository.get_interaction(interaction_id)
            except Exception:
                raise HTTPException(503, "Chat is temporarily unavailable") from None
            if interaction is None:
                raise HTTPException(404, "Conversation not found")
            if str(interaction.get("business_id")) != str(business_id) or interaction.get("channel") != "api":
                raise HTTPException(404, "Conversation not found")
        try:
            reply = await service.handle_turn(interaction_id, request.text)
        except ValueError:
            raise HTTPException(422, "Invalid conversation request") from None
        except Exception:
            raise HTTPException(503, "Chat is temporarily unavailable") from None
        return ChatResponse(
            interaction_id=interaction_id,
            conversation_token=token_for(interaction_id),
            reply=reply,
        )

    return router
