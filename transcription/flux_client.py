import asyncio
import json
from contextlib import suppress

from websockets.asyncio.client import connect

from events import customer_turn
from handoff import handle_customer_turn


async def transcribe(browser, api_key: str, call_id: str, sample_rate: int):
    url = (
        "wss://api.deepgram.com/v2/listen"
        "?model=flux-general-en"
        "&encoding=linear16"
        f"&sample_rate={sample_rate}"
    )

    async with connect(
        url,
        additional_headers={"Authorization": f"Token {api_key}"},
        open_timeout=15,
        close_timeout=5,
    ) as flux:
        seen_turns = set()

        async def receive_transcripts():
            async for raw in flux:
                message = json.loads(raw)

                if message.get("type") == "Connected":
                    await browser.send_json({
                        "type": "ready",
                        "call_id": call_id,
                    })

                elif message.get("type") == "Error":
                    raise RuntimeError(
                        message.get("description", "Flux returned an error")
                    )

                elif message.get("type") == "TurnInfo":
                    text = message.get("transcript", "").strip()
                    index = message["turn_index"]

                    if message.get("event") == "EndOfTurn":
                        if text and index not in seen_turns:
                            event = customer_turn(call_id, index, text)
                            await handle_customer_turn(event)
                            seen_turns.add(index)
                            await browser.send_json(event)
                    else:
                        await browser.send_json({
                            "type": "transcript.partial",
                            "text": text,
                        })

        async def send_audio():
            while True:
                message = await browser.receive()

                if message["type"] == "websocket.disconnect":
                    return

                audio = message.get("bytes")
                if audio is not None:
                    await flux.send(audio)
                elif message.get("text"):
                    command = json.loads(message["text"])
                    if command.get("type") == "stop":
                        await flux.send(json.dumps({"type": "CloseStream"}))
                        return

        receiver = asyncio.create_task(receive_transcripts())
        sender = asyncio.create_task(send_audio())

        try:
            done, _ = await asyncio.wait(
                {receiver, sender},
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Surface any connection or processing errors.
            for task in done:
                task.result()

            if sender in done and not receiver.done():
                # Allow Flux to finish processing buffered audio.
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(receiver), timeout=5)
        finally:
            for task in (sender, receiver):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)
