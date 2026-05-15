"""Live Voice Module — real-time audio + per-utterance compliance scoring.

A FastAPI WebSocket + SSE service that ingests browser microphone audio,
streams it through Azure Speech Service for partial and final transcripts,
and runs the same six-rule compliance auditor as the batch pipeline on
each finalized utterance.

Entrypoint:
    uvicorn custom_extensions.live_voice.server:app --port 8090
"""

__version__ = "0.1.0"
