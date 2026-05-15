"""Smoke test for AzureSTTSession against a local WAV file.

Run from repo root:
    python scripts/test_stt.py path/to/sample-16khz-mono.wav

Expected: prints partial + final transcripts to stdout.
"""
from __future__ import annotations

import asyncio
import sys
import wave
from pathlib import Path

from custom_extensions.live_voice.azure_stt import AzureSTTSession


async def main(wav_path: Path) -> None:
    with wave.open(str(wav_path), "rb") as wf:
        assert wf.getframerate() == 16000, "WAV must be 16kHz mono PCM"
        assert wf.getnchannels() == 1, "WAV must be mono"
        assert wf.getsampwidth() == 2, "WAV must be 16-bit"
        audio = wf.readframes(wf.getnframes())

    stt = AzureSTTSession()
    await stt.start()

    async def reader():
        async for evt in stt.events():
            if evt.type == "partial":
                print(f"  ~ {evt.text}", flush=True)
            elif evt.type == "final":
                print(f"  ✓ {evt.text}", flush=True)
            elif evt.type == "stopped":
                print("session stopped", flush=True)
                break
            elif evt.type == "error":
                print(f"ERROR: {evt.error}", flush=True)
                break

    reader_task = asyncio.create_task(reader())

    # Feed audio in ~80ms chunks (16 kHz * 2 bytes * 0.08s = 2560 bytes)
    chunk_size = 2560
    for i in range(0, len(audio), chunk_size):
        stt.push_audio(audio[i:i + chunk_size])
        await asyncio.sleep(0.08)

    await asyncio.sleep(1.0)
    await stt.stop()
    await reader_task


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python scripts/test_stt.py <wav-path>")
        sys.exit(1)
    asyncio.run(main(Path(sys.argv[1])))
