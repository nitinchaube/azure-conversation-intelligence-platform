"""Thin async-friendly wrapper around the Azure Speech Recognizer.

Why the threading dance: the Speech SDK fires callbacks on its own thread.
We bridge those callbacks safely into the asyncio event loop using
``loop.call_soon_threadsafe``. Without this, ``queue.put_nowait`` from the
SDK thread would race with the consumer on the asyncio thread.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import AsyncIterator, Optional

import azure.cognitiveservices.speech as speechsdk

from .config import SETTINGS

log = logging.getLogger(__name__)


@dataclass
class STTEvent:
    """Single event emitted by the recognizer."""

    type: str            # "partial" | "final" | "stopped" | "error"
    text: str = ""
    offset_ms: int = 0
    error: str = ""


class AzureSTTSession:
    """Streaming STT session backed by Azure Speech ``SpeechRecognizer``.

    Audio in: 16 kHz, 16-bit, mono PCM via ``push_audio(bytes)``.
    Events out: ``async for event in session.events()`` yields STTEvent objects.
    """

    def __init__(self, language: Optional[str] = None) -> None:
        self._loop = asyncio.get_event_loop()
        self._events: "asyncio.Queue[STTEvent]" = asyncio.Queue()
        self._language = language or SETTINGS.speech_language
        self._push_stream: Optional[speechsdk.audio.PushAudioInputStream] = None
        self._recognizer: Optional[speechsdk.SpeechRecognizer] = None
        self._started = False
        self._stopped = False

    # ------------------------------------------------------------------ public
    async def start(self) -> None:
        if self._started:
            return
        stream_format = speechsdk.audio.AudioStreamFormat(
            samples_per_second=16000,
            bits_per_sample=16,
            channels=1,
        )
        self._push_stream = speechsdk.audio.PushAudioInputStream(
            stream_format=stream_format
        )
        audio_config = speechsdk.audio.AudioConfig(stream=self._push_stream)

        speech_config = speechsdk.SpeechConfig(
            subscription=SETTINGS.speech_key,
            region=SETTINGS.speech_region,
        )
        speech_config.speech_recognition_language = self._language
        # Lower the segmentation timeout so finals commit faster after a pause.
        speech_config.set_property(
            speechsdk.PropertyId.Speech_SegmentationSilenceTimeoutMs, "500"
        )

        self._recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config,
            audio_config=audio_config,
        )

        self._recognizer.recognizing.connect(self._on_recognizing)
        self._recognizer.recognized.connect(self._on_recognized)
        self._recognizer.session_stopped.connect(self._on_session_stopped)
        self._recognizer.canceled.connect(self._on_canceled)

        future = self._recognizer.start_continuous_recognition_async()
        await asyncio.get_event_loop().run_in_executor(None, future.get)
        self._started = True
        log.info("AzureSTTSession started (language=%s)", self._language)

    def push_audio(self, audio_bytes: bytes) -> None:
        """Push 16 kHz mono Int16 PCM bytes into the recognizer."""
        if not self._started or self._stopped:
            return
        assert self._push_stream is not None
        self._push_stream.write(audio_bytes)

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        try:
            if self._push_stream is not None:
                self._push_stream.close()
            if self._recognizer is not None:
                future = self._recognizer.stop_continuous_recognition_async()
                await asyncio.get_event_loop().run_in_executor(None, future.get)
        finally:
            log.info("AzureSTTSession stopped")

    async def events(self) -> AsyncIterator[STTEvent]:
        """Async iterator over STTEvent."""
        while True:
            evt = await self._events.get()
            yield evt
            if evt.type in ("stopped", "error"):
                break

    # --------------------------------------------------------- SDK callbacks
    # These run on the Speech SDK thread, NOT the asyncio loop thread.
    # Bridge via call_soon_threadsafe to put events into the asyncio queue safely.
    def _put(self, evt: STTEvent) -> None:
        self._loop.call_soon_threadsafe(self._events.put_nowait, evt)

    def _on_recognizing(self, evt) -> None:
        if evt.result.text:
            self._put(
                STTEvent(
                    type="partial",
                    text=evt.result.text,
                    offset_ms=int(evt.result.offset / 10000),
                )
            )

    def _on_recognized(self, evt) -> None:
        if (
            evt.result.reason == speechsdk.ResultReason.RecognizedSpeech
            and evt.result.text
        ):
            self._put(
                STTEvent(
                    type="final",
                    text=evt.result.text,
                    offset_ms=int(evt.result.offset / 10000),
                )
            )

    def _on_session_stopped(self, evt) -> None:
        self._put(STTEvent(type="stopped"))

    def _on_canceled(self, evt) -> None:
        err = (
            f"{evt.reason}: {evt.error_details}"
            if hasattr(evt, "error_details")
            else str(evt.reason)
        )
        self._put(STTEvent(type="error", error=err))
