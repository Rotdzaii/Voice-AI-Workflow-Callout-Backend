"""Google Cloud Speech-to-Text streaming (async) wrapper with partials.

Requires `google-cloud-speech>=2.24`.
"""
import asyncio
import logging
import os
from typing import AsyncGenerator, Iterable, List, Optional

try:
    from google.cloud import speech_v1p1beta1 as speech  # type: ignore
except Exception:  # pragma: no cover - dependency optional
    speech = None  # type: ignore

logger = logging.getLogger(__name__)

DEFAULT_LANGUAGE = os.getenv("GOOGLE_SPEECH_LANG", "vi-VN")


def _build_speech_contexts(phrase_hints: Optional[List[str]]):
    if not phrase_hints:
        return []
    return [speech.SpeechContext(phrases=phrase_hints)]


async def transcribe_streaming(
    audio_generator: AsyncGenerator[bytes, None],
    phrase_hints: Optional[List[str]] = None,
    language_code: str = DEFAULT_LANGUAGE,
    sample_rate_hz: int = 16000,
    enable_automatic_punctuation: bool = True,
) -> AsyncGenerator[dict, None]:
    """Yield {ok, text, is_final, confidence} dictionaries from Google streaming ASR.

    Expects raw audio bytes (linear16/opus-transcoded) frames from `audio_generator`.
    """
    if speech is None:
        logger.warning("google-cloud-speech not installed; cannot use Google STT")
        return

    client = speech.SpeechAsyncClient()

    speech_contexts = _build_speech_contexts(phrase_hints)
    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        language_code=language_code,
        sample_rate_hertz=sample_rate_hz,
        enable_automatic_punctuation=enable_automatic_punctuation,
        speech_contexts=speech_contexts,
        model="default",
        use_enhanced=True,
    )
    streaming_config = speech.StreamingRecognitionConfig(
        config=config,
        interim_results=True,
        single_utterance=False,
    )

    async def request_iter():
        async for chunk in audio_generator:
            if chunk:
                yield speech.StreamingRecognizeRequest(audio_content=chunk)
        # send a final empty request to flush
        yield speech.StreamingRecognizeRequest()

    try:
        responses = await client.streaming_recognize(
            config=streaming_config,
            requests=request_iter(),
            timeout=60.0,
        )
        async for response in responses:
            if not response.results:
                continue
            for result in response.results:
                alt = result.alternatives[0] if result.alternatives else None
                text = alt.transcript if alt else ""
                confidence = alt.confidence if alt else 0.0
                yield {
                    "ok": True,
                    "text": text,
                    "is_final": result.is_final,
                    "confidence": confidence,
                }
    except Exception as e:
        logger.exception("Google STT streaming failed: %s", e)
        yield {"ok": False, "error": str(e)}
    finally:
        try:
            await client.close()
        except Exception:
            pass
