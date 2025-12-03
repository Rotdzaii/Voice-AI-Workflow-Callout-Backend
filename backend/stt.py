from __future__ import annotations
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def transcribe_bytes(audio_bytes: bytes, model: Optional[str] = None) -> dict:
    """Transcribe an audio bytes payload using HuggingFace `pipeline` (Whisper or other ASR).

    Returns a dict containing at least `text`.
    """
    model = model or os.environ.get("STT_MODEL", "openai/whisper-small")
    try:
        from transformers import pipeline

        asr = pipeline(task="automatic-speech-recognition", model=model)
        # pipeline accepts file-like objects; pass bytes via memoryview wrapper
        import io

        audio_file = io.BytesIO(audio_bytes)
        res = asr(audio_file)
        text = res.get("text") if isinstance(res, dict) else str(res)
        return {"text": text, "ok": True}
    except Exception as e:
        logger.exception("STT transcription failed")
        return {"text": "", "ok": False, "error": str(e)}
