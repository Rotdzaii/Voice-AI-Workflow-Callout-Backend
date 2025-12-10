"""Streaming utilities for incremental Gemini text + TTS audio delivery."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Optional

from fastapi import HTTPException

logger = logging.getLogger("streaming")

try:  # Optional dependency; provide graceful fallback when unavailable
    import edge_tts  # type: ignore
except Exception:  # pragma: no cover - missing optional dependency
    edge_tts = None  # type: ignore

try:
    import google.generativeai as genai  # type: ignore
except Exception:  # pragma: no cover - unit tests may not install SDK
    genai = None  # type: ignore

_GEMINI_CLIENT: Optional["GeminiStreamAdapter"] = None

DEFAULT_GEMINI_MODEL = os.environ.get("GEMINI_STREAM_MODEL") or os.environ.get("LLM_MODEL_NAME", "gemini-2.0-flash")
DEFAULT_TTS_VOICE = os.environ.get("EDGE_TTS_VOICE", "en-US-AriaNeural")
DEFAULT_TTS_RATE = os.environ.get("EDGE_TTS_RATE", "+0%")


def _fake_mode() -> bool:
    return os.environ.get("STREAMING_FAKE_MODE", "0") == "1"


def _fake_text() -> str:
    return os.environ.get("STREAMING_FAKE_RESPONSE", "Xin chào!|Tôi có thể giúp gì cho bạn hôm nay?")


class StreamingError(Exception):
    """Raised when background streaming workers fail."""


@dataclass
class StreamMetadata:
    question: str
    source_ids: list[str]
    groups: list[str]
    topics: list[str]
    scores: list[float]
    latencies: dict[str, float]


class GeminiStreamAdapter:
    """Thin wrapper that exposes Gemini streaming responses via a queue."""

    def __init__(self) -> None:
        key = (
            os.environ.get("GEMINI_KEY")
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )
        if _fake_mode():
            logger.warning("STREAMING_FAKE_MODE enabled – emitting synthetic chunks")
            self._fake = True
            self._model = None
            return
        if not key:
            raise RuntimeError("Gemini API key missing. Set GEMINI_KEY or GEMINI_API_KEY.")
        if genai is None:
            raise RuntimeError("google-generativeai package not installed")
        genai.configure(api_key=key)
        self._model = genai.GenerativeModel(DEFAULT_GEMINI_MODEL)
        self._fake = False

    def start(self, prompt: str, loop: asyncio.AbstractEventLoop, q: asyncio.Queue[Any]) -> None:
        if self._fake:
            chunks = [c.strip() for c in _fake_text().split("|") if c.strip()]

            def _emit_fake() -> None:
                try:
                    for part in chunks:
                        loop.call_soon_threadsafe(q.put_nowait, part)
                        time.sleep(0.05)
                finally:
                    loop.call_soon_threadsafe(q.put_nowait, None)

            threading.Thread(target=_emit_fake, daemon=True).start()
            return

        assert self._model is not None

        def _run() -> None:
            try:
                stream = self._model.generate_content(prompt, stream=True)
                for chunk in stream:
                    text = _extract_text(chunk)
                    if text:
                        loop.call_soon_threadsafe(q.put_nowait, text)
            except Exception as exc:  # pragma: no cover - requires live API
                loop.call_soon_threadsafe(q.put_nowait, StreamingError(str(exc)))
            finally:
                loop.call_soon_threadsafe(q.put_nowait, None)

        threading.Thread(target=_run, daemon=True).start()


def _extract_text(chunk: Any) -> str:
    text = getattr(chunk, "text", None)
    if text:
        return text
    parts = getattr(chunk, "parts", None)
    if parts:
        return "".join(getattr(p, "text", "") for p in parts if getattr(p, "text", None))
    candidates = getattr(chunk, "candidates", None)
    if candidates:
        texts = []
        for cand in candidates:
            for part in getattr(cand, "content", {}).get("parts", []):
                maybe = part.get("text")
                if maybe:
                    texts.append(maybe)
        return "".join(texts)
    return ""


async def stream_events(
    prompt: str,
    metadata: StreamMetadata,
    include_audio: bool = True,
    voice: Optional[str] = None,
    rate: Optional[str] = None,
) -> AsyncGenerator[bytes, None]:
    """Return an SSE-friendly async generator for streaming events."""

    adapter = _get_gemini_adapter()
    event_loop = asyncio.get_running_loop()
    text_queue: asyncio.Queue[Any] = asyncio.Queue()
    audio_queue: asyncio.Queue[Optional[tuple[int, str]]] = asyncio.Queue()
    outbound: asyncio.Queue[Optional[dict[str, Any]]] = asyncio.Queue()
    request_start = time.time()

    await outbound.put(
        {
            "type": "log",
            "event": "request_start",
            "timestamp": request_start,
            "metadata": {
                "question": metadata.question,
                "sources": metadata.source_ids,
                "groups": metadata.groups,
                "topics": metadata.topics,
                "scores": metadata.scores,
                "latencies": metadata.latencies,
            },
        }
    )

    adapter.start(prompt, event_loop, text_queue)

    async def llm_worker() -> None:
        chunk_idx = 0
        while True:
            chunk = await text_queue.get()
            if chunk is None:
                if include_audio:
                    await audio_queue.put(None)
                await outbound.put(
                    {
                        "type": "log",
                        "event": "response_end",
                        "timestamp": time.time(),
                    }
                )
                break
            if isinstance(chunk, StreamingError):
                await outbound.put({"type": "error", "message": str(chunk)})
                if include_audio:
                    await audio_queue.put(None)
                break
            chunk_idx += 1
            ts = time.time()
            await outbound.put(
                {
                    "type": "text",
                    "sequence": chunk_idx,
                    "timestamp": ts,
                    "chunk": chunk,
                }
            )
            await outbound.put(
                {
                    "type": "log",
                    "event": "received_text_chunk",
                    "sequence": chunk_idx,
                    "timestamp": ts,
                }
            )
            if include_audio:
                await audio_queue.put((chunk_idx, chunk))

    async def tts_worker() -> None:
        if not include_audio:
            return
        if edge_tts is None and not _fake_mode():
            raise HTTPException(status_code=500, detail="edge-tts not installed; cannot stream audio")
        seq = 0
        while True:
            item = await audio_queue.get()
            if item is None:
                await outbound.put(
                    {
                        "type": "log",
                        "event": "tts_complete",
                        "timestamp": time.time(),
                    }
                )
                break
            chunk_id, text = item
            await outbound.put(
                {
                    "type": "log",
                    "event": "tts_start_chunk",
                    "sequence": chunk_id,
                    "timestamp": time.time(),
                }
            )
            async for audio_chunk in _synthesize_chunk(text, voice or DEFAULT_TTS_VOICE, rate or DEFAULT_TTS_RATE):
                seq += 1
                await outbound.put(
                    {
                        "type": "audio",
                        "sequence": chunk_id,
                        "timestamp": time.time(),
                        "chunk": audio_chunk,
                    }
                )
                await outbound.put(
                    {
                        "type": "log",
                        "event": "tts_chunk_emitted",
                        "sequence": chunk_id,
                        "timestamp": time.time(),
                    }
                )

    async def aggregator(workers: list[asyncio.Task[None]]) -> None:
        try:
            await asyncio.gather(*workers)
        finally:
            await outbound.put(None)

    workers = [asyncio.create_task(llm_worker())]
    if include_audio:
        workers.append(asyncio.create_task(tts_worker()))
    asyncio.create_task(aggregator(workers))

    while True:
        event = await outbound.get()
        if event is None:
            break
        yield _sse(event)


def _sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


async def _synthesize_chunk(text: str, voice: str, rate: str) -> AsyncGenerator[str, None]:
    if not text.strip():
        return
    if _fake_mode() or edge_tts is None:
        # fallback for tests: emit pseudo audio chunk
        yield base64.b64encode(text.encode("utf-8")).decode("ascii")
        return
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
    async for msg in communicate.stream():  # pragma: no cover - requires network
        if msg["type"] == "audio":
            yield base64.b64encode(msg["data"]).decode("ascii")


def _get_gemini_adapter() -> GeminiStreamAdapter:
    global _GEMINI_CLIENT
    if _GEMINI_CLIENT is None:
        _GEMINI_CLIENT = GeminiStreamAdapter()
    return _GEMINI_CLIENT


def reset_adapter_for_tests() -> None:
    """Test helper to reset singleton state."""
    global _GEMINI_CLIENT
    _GEMINI_CLIENT = None
