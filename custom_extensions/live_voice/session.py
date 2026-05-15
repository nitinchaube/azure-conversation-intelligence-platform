"""Per-call live session orchestrator."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Dict, List, Optional

from .azure_stt import AzureSTTSession, STTEvent
from .rules import ComplianceScore, score

log = logging.getLogger(__name__)

# Re-score compliance at most once per 4 seconds, regardless of utterance rate.
COMPLIANCE_COOLDOWN_S = 4.0


@dataclass
class LiveSession:
    session_id: str
    stt: AzureSTTSession = field(default_factory=AzureSTTSession)
    transcript: List[str] = field(default_factory=list)          # finalized utterances
    event_queue: "asyncio.Queue[dict]" = field(default_factory=asyncio.Queue)
    _last_score_at: float = 0.0
    _scoring_task: Optional[asyncio.Task] = None
    _stt_task: Optional[asyncio.Task] = None
    _started: bool = False
    _closed: bool = False

    async def start(self) -> None:
        if self._started:
            return
        await self.stt.start()
        self._started = True
        self._stt_task = asyncio.create_task(self._consume_stt())
        await self.event_queue.put(
            {"type": "session_started", "session_id": self.session_id}
        )

    async def push_audio(self, audio_bytes: bytes) -> None:
        self.stt.push_audio(audio_bytes)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.stt.stop()
        except Exception:
            log.exception("Error stopping STT")
        if self._scoring_task and not self._scoring_task.done():
            self._scoring_task.cancel()
        await self.event_queue.put(
            {"type": "session_ended", "session_id": self.session_id}
        )

    async def events(self) -> AsyncIterator[dict]:
        """SSE consumer pulls from here."""
        while True:
            evt = await self.event_queue.get()
            yield evt
            if evt.get("type") == "session_ended":
                break

    # ----------------------------------------------------------- internals
    async def _consume_stt(self) -> None:
        async for evt in self.stt.events():
            await self._handle_stt_event(evt)

    async def _handle_stt_event(self, evt: STTEvent) -> None:
        if evt.type == "partial":
            await self.event_queue.put(
                {
                    "type": "transcript_partial",
                    "text": evt.text,
                    "offset_ms": evt.offset_ms,
                }
            )
        elif evt.type == "final":
            self.transcript.append(evt.text)
            await self.event_queue.put(
                {
                    "type": "transcript_final",
                    "text": evt.text,
                    "offset_ms": evt.offset_ms,
                    "full_transcript": " ".join(self.transcript),
                }
            )
            await self._maybe_score()
        elif evt.type == "error":
            await self.event_queue.put({"type": "error", "message": evt.error})
        elif evt.type == "stopped":
            await self.close()

    async def _maybe_score(self) -> None:
        now = time.monotonic()
        if now - self._last_score_at < COMPLIANCE_COOLDOWN_S:
            return
        if self._scoring_task and not self._scoring_task.done():
            return  # one in flight, skip
        self._last_score_at = now
        self._scoring_task = asyncio.create_task(self._run_scoring())

    async def _run_scoring(self) -> None:
        transcript = " ".join(self.transcript)
        if not transcript.strip():
            return
        try:
            result: ComplianceScore = await score(transcript)
        except Exception as e:
            log.exception("Compliance scoring failed")
            await self.event_queue.put(
                {"type": "compliance_error", "message": str(e)}
            )
            return
        await self.event_queue.put(
            {
                "type": "compliance_update",
                "overall": result.overall,
                "rules": [
                    {
                        "rule_id": r.rule_id,
                        "name": r.name,
                        "severity": r.severity,
                        "passed": r.passed,
                        "not_applicable": r.not_applicable,
                        "rationale": r.rationale,
                    }
                    for r in result.per_rule
                ],
            }
        )


# Process-wide session registry.
SESSIONS: Dict[str, LiveSession] = {}


async def get_or_create_session(session_id: str) -> LiveSession:
    sess = SESSIONS.get(session_id)
    if sess is None:
        sess = LiveSession(session_id=session_id)
        SESSIONS[session_id] = sess
        await sess.start()
    return sess


async def end_session(session_id: str) -> None:
    sess = SESSIONS.pop(session_id, None)
    if sess:
        await sess.close()
