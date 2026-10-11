"""SSA-023 phone transport: Twilio webhook and ConversationRelay WebSocket.

Twilio transcribes the caller and speaks our text, so this module only moves
text. Each completed utterance goes to the same ConversationService that
POST /chat uses; nothing here books, authorizes or calls a model itself.
Twilio request signatures are not validated (optional for the demo; see README).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from xml.sax.saxutils import quoteattr

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse, Response

from backend.services.conversation import CALLBACK_LINE

logger = logging.getLogger("uvicorn.error")

WELCOME_GREETING = (
    "Hello. You are speaking with our automated appointment assistant. How can I help?"
)
FILLER_LINE = "One moment."
UNAVAILABLE_LINE = (
    "Sorry, our assistant is unavailable right now. Nothing has been booked. "
    "Please call back later."
)
FILLER_AFTER_SECONDS = 1.5
# Twilio does not document whether `end` waits for queued speech, so the
# callback line gets time to be spoken before the session is ended.
SPEECH_CHARACTERS_PER_SECOND = 14


def twiml(host: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response><Connect><ConversationRelay "
        f"url={quoteattr(f'wss://{host}/voice/stream')} "
        f"welcomeGreeting={quoteattr(WELCOME_GREETING)}"
        "/></Connect></Response>"
    )


def _run_turn(service: Any, interaction_id: Any, text: str) -> str:
    # The service's model and gate calls block, so the turn gets its own thread
    # and event loop; the socket stays free to speak the filler line meanwhile.
    return asyncio.run(service.handle_turn(interaction_id, text))


router = APIRouter()


def approval_twiml(name: str = "Seyun", service: str = "haircut") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response><Pause length=\"1\"/>"
        "<Say voice=\"Polly.Joanna\">"
        f"Hello {name}. This is the automated assistant from the salon. "
        f"Our staff has reviewed and approved your {service} appointment. "
        "Your booking is confirmed. We look forward to seeing you."
        "</Say></Response>"
    )


@router.api_route("/voice/approval", methods=["GET", "POST"])
def approval_endpoint(name: str = "Seyun", service: str = "haircut"):
    return Response(content=approval_twiml(name, service), media_type="text/xml")


@router.post("/voice/incoming")
def incoming(request: Request):
    """Twilio's "a call comes in" webhook. The host is server configuration."""
    host = request.app.state.settings.public_host
    if not host:
        return PlainTextResponse("PUBLIC_HOST is not configured", status_code=503)
    return Response(content=twiml(host), media_type="text/xml")


@router.websocket("/voice/stream")
async def stream(websocket: WebSocket):
    state = websocket.app.state
    settings = state.settings
    service = getattr(state, "conversation_service", None)
    repository = getattr(state, "conversation_repository", None)
    ready = service is not None and getattr(state, "chat_ready", False)
    interaction_id = None
    await websocket.accept()

    async def say(text: str) -> None:
        await websocket.send_json({"type": "text", "token": text, "last": True})

    async def reply_to(text: str) -> bool:
        """Speak the reply to one utterance. Returns False when the call should end."""
        if settings.voice_echo:
            await say(f"You said: {text}")
            return True
        if not ready or interaction_id is None:
            await say(UNAVAILABLE_LINE)
            return False
        turn = asyncio.create_task(asyncio.to_thread(_run_turn, service, interaction_id, text))
        done, _ = await asyncio.wait({turn}, timeout=FILLER_AFTER_SECONDS)
        if not done:
            await say(FILLER_LINE)
        try:
            reply = await turn
        except Exception as error:
            logger.error("Voice turn failed: %s", type(error).__name__)
            await say(UNAVAILABLE_LINE)
            return False
        await say(reply)
        return reply != CALLBACK_LINE

    try:
        while True:
            try:
                message = json.loads(await websocket.receive_text())
            except json.JSONDecodeError:
                continue
            kind = message.get("type") if isinstance(message, dict) else None
            if kind == "setup":
                if interaction_id is None and ready and not settings.voice_echo:
                    try:
                        interaction_id = await asyncio.to_thread(
                            repository.create_interaction,
                            settings.business_id,
                            channel="phone",
                            external_reference=message.get("callSid") or None,
                            callback_number=message.get("from") or None,
                        )
                    except Exception as error:
                        logger.error("Voice call could not be opened: %s", type(error).__name__)
            elif kind == "prompt":
                # Only completed utterances; partial transcripts are not appended.
                text = str(message.get("voicePrompt") or "").strip()
                if not message.get("last") or not text:
                    continue
                if not await reply_to(text[:4000]):
                    await asyncio.sleep(len(CALLBACK_LINE) / SPEECH_CHARACTERS_PER_SECOND)
                    await websocket.send_json({"type": "end"})
                    break
            elif kind == "interrupt":
                # The turn already ran; only the spoken part was cut short.
                logger.info("Voice reply interrupted after %s ms", message.get("durationUntilInterruptMs"))
            elif kind == "error":
                logger.error("ConversationRelay error: %s", str(message.get("description"))[:200])
    except WebSocketDisconnect:
        pass
