# Live microphone transcription

Captures browser microphone audio and streams it to Deepgram Flux through
a Python FastAPI server.

## Setup

Use Python 3.12 with an active virtual environment.
From the repository root, run:

    python -m pip install -r transcription/requirements.txt

Set DEEPGRAM_API_KEY in the repository-root .env file.
Use your generated secret, not the key name. Do not commit .env.

## Run

From the repository root:

    cd transcription
    python -m uvicorn server:app --reload --host 127.0.0.1 --port 8000

Open http://127.0.0.1:8000/ and allow microphone access.
The Flux connection uses certifi for certificate verification.

## Test

Start the microphone, speak, and pause. Check that completed transcripts
appear on the page and JSON events print in the terminal.

## Next step

Connect handle_customer_turn() in handoff.py to the team's workflow.
Currently, this function only prints completed transcript events.
