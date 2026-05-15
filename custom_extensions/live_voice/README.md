# Live Voice Module

A real-time audio path bolted onto the Conversation Intelligence Platform.
Bidirectional WebSocket ingests browser microphone audio, streams it
through Azure Speech Service for partial and final transcripts, and runs
the same six-rule compliance auditor as the batch pipeline on each
finalized utterance. Live transcript and live compliance scores are
pushed back to the demo UI over Server-Sent Events.

## Architecture

```
Browser mic
    │  getUserMedia → AudioContext (Float32, 48kHz)
    │  ScriptProcessorNode → downsample 48k→16k + Int16 LE PCM
    ▼
WebSocket /api/voice/stream/{session_id}   ◀── outbound audio
EventSource /api/voice/events/{session_id} ──▶ inbound events (SSE)
    │
    ▼
FastAPI (uvicorn :8090)
    ├─ azure_stt.py   — Azure SpeechRecognizer + PushAudioInputStream
    ├─ rules.py       — 6 compliance rules via Azure OpenAI
    ├─ session.py     — LiveSession orchestrator (per-call state)
    └─ server.py      — WebSocket + SSE endpoints, static demo UI
```

## Run

From the repo root with the live-voice venv active:

```bash
python -m venv .venv-livevoice
source .venv-livevoice/bin/activate
pip install -r custom_extensions/live_voice/requirements.txt

# .env must contain:
#   AZURE_SPEECH_KEY=...
#   AZURE_SPEECH_REGION=eastus           (or wherever you provisioned)
#   AZURE_SPEECH_LANGUAGE=en-US          (optional, defaults to en-US)
#   AZURE_OPENAI_ENDPOINT=https://...
#   AZURE_OPENAI_DEPLOYMENT=gpt-4o-mini  (or AZURE_AI_AGENT_MODEL_DEPLOYMENT_NAME)
#   AZURE_OPENAI_API_VERSION=2024-10-21  (optional)
#   AZURE_OPENAI_API_KEY=...             (optional — falls back to DefaultAzureCredential)

uvicorn custom_extensions.live_voice.server:app --host 0.0.0.0 --port 8090 --reload
```

Open <http://localhost:8090/> and click **Start Recording**.

## Files

| File | Purpose |
|---|---|
| `config.py` | Env loading with API-key or AAD auth |
| `azure_stt.py` | Async wrapper around Azure Speech `PushAudioInputStream` |
| `rules.py` | Per-utterance 6-rule severity-weighted scorer |
| `session.py` | `LiveSession` — bridges STT events to compliance and SSE |
| `server.py` | FastAPI app: WebSocket audio in, SSE events out, static UI |
| `static/index.html` | Demo page |
| `static/recorder.js` | Mic capture, 48k→16k downsample, WS sender |
| `static/style.css` | UI styles |

## Smoke tests

```bash
# STT round-trip against a local 16kHz mono WAV
python scripts/test_stt.py path/to/sample-16khz-mono.wav

# Compliance scoring round-trip
python scripts/test_rules.py
```

## Data flow per utterance

1. Browser captures mic, downsamples to 16kHz PCM, sends binary WebSocket
   frames every ~85ms.
2. Server pushes audio bytes into `PushAudioInputStream`.
3. Azure Speech emits `recognizing` (partial) and `recognized` (final) events.
4. Both partial and final transcripts → SSE event queue → browser.
5. On `recognized` (final utterance), server triggers compliance scoring via
   Azure OpenAI on the rolling transcript buffer.
6. Compliance result → SSE event queue → browser updates the panel.

Scoring is rate-limited to once every 4 seconds (`COMPLIANCE_COOLDOWN_S`
in `session.py`) so the LLM call never blocks the transcript pipeline.

## Session lifecycle

State lives in `LiveSession` keyed by `session_id`. When the WebSocket
closes the session is torn down: STT stopped, push stream closed,
compliance worker cancelled, SSE subscribers notified via a final
`session_ended` event.
