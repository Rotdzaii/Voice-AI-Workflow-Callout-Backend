import importlib
import json
import sys
from pathlib import Path

import pytest

# Force AnyIO plugin to use asyncio backend to avoid extra deps
@pytest.fixture
def anyio_backend():
    return "asyncio"

# Ensure repo root is importable when tests run from arbitrary cwd
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import streaming as streaming_module


@pytest.mark.anyio("asyncio")
async def test_stream_events_fake_mode(monkeypatch):
    """Streaming emits incremental text and audio chunks in fake mode."""
    monkeypatch.setenv("STREAMING_FAKE_MODE", "1")
    monkeypatch.setenv("STREAMING_FAKE_RESPONSE", "chunk-one|chunk-two")

    streaming = importlib.reload(streaming_module)
    streaming.reset_adapter_for_tests()

    metadata = streaming.StreamMetadata(
        question="hello",
        source_ids=["1"],
        groups=["general"],
        topics=["demo"],
        scores=[0.99],
        latencies={"retriever": 0.01},
    )

    events = []
    async for payload in streaming.stream_events(
        prompt="demo prompt",
        metadata=metadata,
        include_audio=True,
    ):
        text = payload.decode("utf-8").strip()
        assert text.startswith("data: ")
        data = json.loads(text[len("data: ") :])
        events.append(data)

    types = [evt.get("type") for evt in events]
    assert "text" in types, "Expected streamed text chunks"
    assert "audio" in types, "Expected streamed audio chunks"

    text_chunks = [evt for evt in events if evt.get("type") == "text"]
    audio_chunks = [evt for evt in events if evt.get("type") == "audio"]
    assert [chunk["chunk"] for chunk in text_chunks] == ["chunk-one", "chunk-two"]
    # Audio is base64 encoded, ensure chunk references exist for both text chunks
    assert {evt["sequence"] for evt in audio_chunks} == {1, 2}
