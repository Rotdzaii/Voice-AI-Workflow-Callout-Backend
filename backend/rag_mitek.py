"""Utility wrapper ported from the notebook `ragmitek (2).ipynb`.

This module re-uses core RAG building blocks from `rag.rag` and
adds a small Google TTS helper and CLI runner for convenience.

It is intentionally non-invasive: it doesn't change the existing
`rag.rag` module or backend API; it just provides a place to run
the notebook-style workflow from the `backend/` package.
"""
from __future__ import annotations
import os
import time
import json
import logging
from datetime import datetime
from typing import Optional, Tuple

try:
    # Reuse core functions already present in the repo
    from rag.rag import prepare_clean_chunks, build_rag_system, GeminiLLM, LLM_MODEL_NAME
except Exception:
    # If rag.rag is missing or broken we still want import to succeed
    prepare_clean_chunks = None
    build_rag_system = None
    GeminiLLM = None
    LLM_MODEL_NAME = os.environ.get("LLM_MODEL_NAME", "gemini-2.0-flash")

# Optional: Google Cloud TTS (only used if credentials are available)
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")


def synthesize_speech(text: str, base_filename: Optional[str] = None) -> Tuple[Optional[bytes], Optional[str], float]:
    """Synthesize speech using Google Cloud Text-to-Speech if available.

    Returns (audio_bytes, file_path_or_url, latency_seconds).
    If TTS_STORAGE=supabase, uploads to Supabase Storage and returns URL.
    Otherwise stores locally if AUDIO_OUTPUT_DIR is set.
    If TTS is not available or credentials missing returns (None, None, 0.0).
    """
    if not GOOGLE_CREDENTIALS:
        return None, None, 0.0

    try:
        from google.cloud import texttospeech
    except Exception:
        logging.getLogger("uvicorn.error").warning("google.cloud.texttospeech not installed")
        return None, None, 0.0

    if not text.strip():
        return None, None, 0.0

    client = texttospeech.TextToSpeechClient()
    synthesis_input = texttospeech.SynthesisInput(text=text)

    voice = texttospeech.VoiceSelectionParams(language_code=os.environ.get("TTS_LANGUAGE_CODE", "vi-VN"),
                                              name=os.environ.get("TTS_VOICE_NAME", "vi-VN-Standard-A"))
    audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3)

    t0 = time.time()
    resp = client.synthesize_speech(input=synthesis_input, voice=voice, audio_config=audio_config)
    audio_bytes = resp.audio_content
    latency = time.time() - t0

    # Determine storage backend
    storage = os.environ.get("TTS_STORAGE", "local").lower()
    
    if storage == "supabase":
        # Upload to Supabase Storage
        try:
            from . import supabase_client
            if base_filename:
                path = f"tts/{base_filename}.mp3"
                url = supabase_client.upload_tts_audio(audio_bytes, path)
                logging.getLogger("uvicorn").info(f"TTS uploaded to Supabase: {path}")
                return audio_bytes, url, latency
        except Exception as e:
            logging.getLogger("uvicorn.error").warning(f"Supabase upload failed, falling back to local: {e}")
    
    # Fallback: store locally
    if base_filename:
        out_dir = os.environ.get("AUDIO_OUTPUT_DIR", "tts_outputs")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"{base_filename}.mp3")
        with open(path, "wb") as f:
            f.write(audio_bytes)
        return audio_bytes, path, latency

    return audio_bytes, None, latency


def prepare_and_build() -> Optional[object]:
    """Run cleaning/chunking and build the Chroma vectorstore.

    Returns the vectorstore object or None on failure.
    """
    if prepare_clean_chunks is None or build_rag_system is None:
        logging.getLogger("uvicorn.error").error("rag.rag utilities not available")
        return None

    clean_file = prepare_clean_chunks()
    vs = build_rag_system(clean_file)
    return vs


def run_cli_chat():
    """Simple CLI runner that initialises LLM + vectorstore and delegates
    to `rag.rag.chat` when available. This mirrors the notebook's `chat()`.
    """
    try:
        import rag.rag as rag_module
    except Exception as e:
        logging.getLogger("uvicorn.error").exception("Failed to import rag.rag: %s", e)
        return

    try:
        llm, vectorstore = rag_module.init_rag()
    except Exception as e:
        logging.getLogger("uvicorn.error").exception("init_rag failed: %s", e)
        return

    # reuse the interactive chat from rag.rag which already handles logs
    try:
        rag_module.chat(llm, vectorstore)
    except Exception as e:
        logging.getLogger("uvicorn.error").exception("chat loop failed: %s", e)


if __name__ == "__main__":
    # Allow running from command line for convenience
    print("Starting rag_mitek CLI. This will build or load the Chroma DB and start an interactive chat.")
    run_cli_chat()
