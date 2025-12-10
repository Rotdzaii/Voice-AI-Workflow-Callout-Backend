"""Utility script to list Gemini models available to the current API key."""
from __future__ import annotations

import os
import sys
from typing import Iterable

from dotenv import load_dotenv

try:  # Local helper script, so keep import guarded
    import google.generativeai as genai
except ImportError:  # pragma: no cover - helper script guard
    sys.stderr.write("Missing dependency 'google-generativeai'. Install it first.\n")
    raise SystemExit(2)

POSSIBLE_KEYS = ("GEMINI_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")


def _resolve_api_key() -> str | None:
    for env_name in POSSIBLE_KEYS:
        value = os.environ.get(env_name)
        if value:
            return value
    return None


def _extract_model_names(models: Iterable[object]) -> list[str]:
    names: list[str] = []
    for model in models:
        name = getattr(model, "name", None) or getattr(model, "display_name", None)
        if not name:
            name = str(model)
        names.append(name)
    return sorted(set(names))


def main() -> int:
    load_dotenv()
    api_key = _resolve_api_key()
    if not api_key:
        sys.stderr.write(
            "❌ Gemini API key not found. Set one of: {}\n".format(", ".join(POSSIBLE_KEYS))
        )
        return 1

    try:
        genai.configure(api_key=api_key)
        models = list(genai.list_models())
    except Exception as exc:
        sys.stderr.write(f"❌ Failed to list models: {exc}\n")
        return 2

    names = _extract_model_names(models)
    if not names:
        print("⚠ No models were returned for this API key.")
        return 0

    print("✅ Models available to this API key:")
    for idx, name in enumerate(names, start=1):
        print(f"{idx:02d}. {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
