"""Test script: decode WebM/Opus -> PCM, run webrtcvad, and call transcribe_bytes().

Usage:
    python scripts/test_stt_decode.py path/to/sample.webm

This script requires:
 - ffmpeg on PATH
 - webrtcvad installed in the Python environment
 - GOOGLE_APPLICATION_CREDENTIALS set if using Google STT
"""
import sys
import os
import argparse
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from backend import stt

try:
    import webrtcvad
except Exception:
    webrtcvad = None


def read_file_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def run_vad_on_pcm(pcm_bytes: bytes, aggressiveness: int = 2) -> bool:
    if webrtcvad is None:
        logger.warning("webrtcvad not installed; skipping VAD check")
        return False
    vad = webrtcvad.Vad(aggressiveness)
    sample_rate = 16000
    frame_ms = 30
    bytes_per_sample = 2
    frame_size = int(sample_rate * (frame_ms / 1000.0) * bytes_per_sample)
    for start in range(0, len(pcm_bytes), frame_size):
        frame = pcm_bytes[start:start+frame_size]
        if len(frame) < frame_size:
            break
        try:
            if vad.is_speech(frame, sample_rate):
                return True
        except Exception:
            continue
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("audio_file", help="Path to WebM/Opus or other audio file to test")
    args = parser.parse_args()

    if not os.path.exists(args.audio_file):
        logger.error("Audio file not found: %s", args.audio_file)
        sys.exit(2)

    data = read_file_bytes(args.audio_file)
    logger.info("Read %d bytes from %s", len(data), args.audio_file)

    # Try decode to PCM via helper
    pcm = stt._decode_to_pcm_16k(data)
    if pcm:
        logger.info("Decoded to PCM: %d bytes", len(pcm))
        detected = run_vad_on_pcm(pcm)
        logger.info("VAD speech detected: %s", detected)
    else:
        logger.warning("Could not decode to PCM; skipping VAD and sending raw bytes to STT")

    # Call transcribe_bytes (this will decode again if necessary)
    res = stt.transcribe_bytes(data)
    logger.info("Transcription result: ok=%s confidence=%s text=%s", res.get("ok"), res.get("confidence"), res.get("text"))


if __name__ == "__main__":
    main()
