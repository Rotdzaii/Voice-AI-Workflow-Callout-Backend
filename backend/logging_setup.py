from __future__ import annotations
import logging
import os
from prometheus_client import Counter, CollectorRegistry

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")


def configure_logging():
    level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # reduce noisy debug logs from third-party libs if needed
    logging.getLogger("asyncio").setLevel(logging.WARNING)


# Prometheus metrics (module-level so other modules can import)
registry = CollectorRegistry()
calls_started = Counter("voiceai_calls_started_total", "Total calls started", registry=registry)
conversation_logs_inserted = Counter("voiceai_conversation_logs_total", "Conversation logs written", registry=registry)
stt_requests = Counter("voiceai_stt_requests_total", "STT requests processed", registry=registry)
tts_requests = Counter("voiceai_tts_requests_total", "TTS requests processed", registry=registry)


def get_registry() -> CollectorRegistry:
    return registry
