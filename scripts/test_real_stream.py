"""Ad-hoc script to observe real-time streaming from the backend."""
from __future__ import annotations

import argparse
import sys
import time

import requests


DEFAULT_URL = "http://localhost:8000/stream/sse"
DEFAULT_PROMPT = "Mitek có dịch vụ nào?"


def run(url: str, prompt: str) -> None:
    payload = {"question": prompt, "include_audio": False}
    start = time.perf_counter()

    with requests.post(url, json=payload, stream=True, timeout=None) as resp:
        resp.raise_for_status()
        first_chunk_at = None

        for chunk in resp.iter_content(chunk_size=None):
            if not chunk:
                continue
            now = time.perf_counter()
            if first_chunk_at is None:
                first_chunk_at = now
                print(f"TTFB: {first_chunk_at - start:.2f}s")

            elapsed = now - start
            text = chunk.decode("utf-8", errors="ignore")
            sys.stdout.write(f"[+{elapsed:.2f}s] {text}")
            sys.stdout.flush()

    total = time.perf_counter() - start
    print(f"\nTotal elapsed: {total:.2f}s")


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe SSE streaming behavior")
    parser.add_argument("--url", default=DEFAULT_URL, help="Streaming endpoint URL")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Prompt to send")
    args = parser.parse_args()
    run(args.url, args.prompt)


if __name__ == "__main__":
    main()
