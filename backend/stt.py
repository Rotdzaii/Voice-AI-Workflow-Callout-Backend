from __future__ import annotations
import os
import logging
from typing import Optional
import subprocess
import shutil

logger = logging.getLogger(__name__)


def transcribe_bytes(audio_bytes: bytes, language_code: str = "vi-VN") -> dict:
    """Transcribe audio bytes using Google Speech-to-Text API.
    
    Args:
        audio_bytes: Audio data in WebM, WAV, or other supported format
        language_code: BCP-47 language code (default: vi-VN for Vietnamese)
        
    Returns:
        dict with keys: text (str), ok (bool), error (str, optional)
    """
    try:
        from google.cloud import speech

        client = speech.SpeechClient()

        # Try to decode input to 16kHz LINEAR16 PCM using ffmpeg for better accuracy
        pcm_bytes = None
        try:
            pcm_bytes = _decode_to_pcm_16k(audio_bytes)
        except Exception as e:
            logger.warning(f"ffmpeg decode to PCM failed: {e}; falling back to raw bytes")

        if pcm_bytes:
            audio = speech.RecognitionAudio(content=pcm_bytes)
            config = speech.RecognitionConfig(
                encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=16000,
                language_code=language_code,
                enable_automatic_punctuation=True,
            )
            # prefer enhanced model for better accuracy on short utterances
            try:
                config.use_enhanced = True
                config.model = "latest_short"
            except Exception:
                # some google client versions may set these differently; ignore if unsupported
                pass
        else:
            # Fallback: send raw bytes as WEBM_OPUS (what browser sends). May be less accurate.
            audio = speech.RecognitionAudio(content=audio_bytes)
            config = speech.RecognitionConfig(
                encoding=speech.RecognitionConfig.AudioEncoding.WEBM_OPUS,
                sample_rate_hertz=48000,
                language_code=language_code,
                enable_automatic_punctuation=True,
            )

        response = client.recognize(config=config, audio=audio)

        if response.results:
            text = " ".join([result.alternatives[0].transcript for result in response.results])
            confidence = response.results[0].alternatives[0].confidence if response.results else 0.0
            logger.info(f"STT success: '{text}' (confidence: {confidence:.2f})")
            return {"text": text, "ok": True, "confidence": confidence}
        else:
            logger.warning("STT returned no results (silence or noise)")
            return {"text": "", "ok": True, "confidence": 0.0}

    except Exception as e:
        logger.exception("STT transcription failed")
        return {"text": "", "ok": False, "error": str(e)}


def _decode_to_pcm_16k(input_bytes: bytes) -> Optional[bytes]:
    """Decode input audio bytes (webm/opus, etc.) to 16kHz s16le PCM via ffmpeg (sync).

    Returns raw PCM bytes or None if ffmpeg not available or decode fails.
    """
    if not input_bytes:
        return None

    if not shutil.which("ffmpeg"):
        logger.warning("ffmpeg not found on PATH; cannot decode to PCM")
        return None

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        "pipe:0",
        # apply simple preprocessing: loudness normalization + spectral denoise (afftdn)
        "-af",
        "loudnorm,afftdn",
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "-ac",
        "1",
        "-ar",
        "16000",
        "pipe:1",
    ]

    try:
        proc = subprocess.run(cmd, input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if proc.returncode != 0:
            logger.warning(f"ffmpeg decode failed (rc={proc.returncode}): {proc.stderr.decode(errors='ignore')}")
            return None
        return proc.stdout
    except Exception as e:
        logger.exception(f"Exception while running ffmpeg: {e}")
        return None
