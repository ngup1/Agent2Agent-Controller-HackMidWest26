import asyncio
import logging
import os
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from flux_client import transcribe

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT.parent / ".env")
app = FastAPI()
logger = logging.getLogger("uvicorn.error")

ALLOWED_ORIGINS = {
    "http://localhost:8000",
    "http://127.0.0.1:8000",
}


@app.get("/")
async def home():
    return FileResponse(ROOT / "index.html")


@app.get("/microphone.js")
async def microphone_script():
    return FileResponse(
        ROOT / "microphone.js",
        media_type="application/javascript",
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.websocket("/audio")
async def audio_socket(browser: WebSocket):
    if browser.headers.get("origin") not in ALLOWED_ORIGINS:
        await browser.close(code=1008)
        return

    await browser.accept()

    try:
        api_key = os.getenv("DEEPGRAM_API_KEY", "")
        if not api_key or api_key == "replace_with_your_key":
            raise ValueError("Add your Deepgram API key to .env first.")

        config = await asyncio.wait_for(browser.receive_json(), timeout=10)
        sample_rate = config.get("sample_rate")

        if sample_rate not in {8000, 16000, 24000, 44100, 48000}:
            raise ValueError("Unsupported microphone sample rate.")

        await transcribe(
            browser,
            api_key,
            call_id=str(uuid4()),
            sample_rate=sample_rate,
        )
    except WebSocketDisconnect:
        pass
    except Exception as error:
        logger.error("Transcription failed: %s", error)
        with suppress(Exception):
            await browser.send_json({
                "type": "error",
                "message": "Transcription failed. Check the Python terminal.",
            })
    finally:
        with suppress(Exception):
            await browser.close()
