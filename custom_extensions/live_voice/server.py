"""FastAPI server hosting the live voice WebSocket + SSE endpoints."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from .session import end_session, get_or_create_session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
log = logging.getLogger(__name__)

app = FastAPI(title="CIP Live Voice", version="0.1.0")

# CORS: dev-friendly; tighten for prod.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


@app.websocket("/api/voice/stream/{session_id}")
async def stream(ws: WebSocket, session_id: str) -> None:
    await ws.accept()
    log.info("WS open session=%s", session_id)
    sess = await get_or_create_session(session_id)
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            data = msg.get("bytes")
            if data:
                await sess.push_audio(data)
            elif msg.get("text") == "__end__":
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("WS error session=%s", session_id)
    finally:
        log.info("WS close session=%s", session_id)
        await end_session(session_id)


@app.get("/api/voice/events/{session_id}")
async def events(session_id: str) -> EventSourceResponse:
    sess = await get_or_create_session(session_id)

    async def gen():
        async for evt in sess.events():
            yield {"event": evt.get("type", "message"), "data": json.dumps(evt)}

    return EventSourceResponse(gen())
