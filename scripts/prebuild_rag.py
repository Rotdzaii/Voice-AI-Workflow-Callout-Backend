#!/usr/bin/env python3
"""
Prebuild Chroma DB offline for the RAG system.

Usage:
  - Activate your venv (optional)
  - Run: `python scripts/prebuild_rag.py`

This script will:
  1. Use `rag.prepare_clean_chunks()` to create cleaned chunk CSV if missing.
  2. Call `rag.build_rag_system(clean_file)` to compute embeddings and persist Chroma DB.

This script deliberately avoids initialising the LLM so it does not need GEMINI key.
"""

import logging
import time
import sys
from pathlib import Path

logger = logging.getLogger("prebuild_rag")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main():
    try:
        # import the rag helper module by loading the file directly (no package __init__ required)
        from importlib.util import spec_from_file_location, module_from_spec
        repo_root = Path(__file__).resolve().parent.parent
        rag_path = repo_root / "rag" / "rag.py"
        if not rag_path.exists():
            logger.error("rag.py not found at expected path: %s", rag_path)
            sys.exit(2)
        spec = spec_from_file_location("rag_module", str(rag_path))
        rag = module_from_spec(spec)
        spec.loader.exec_module(rag)
        logger.info("Imported rag module from %s", rag_path)
    except Exception as e:
        logger.exception("Failed to load rag.rag from file: %s", e)
        sys.exit(2)

    try:
        # ensure clean chunk file exists (prepare if missing)
        if not Path(rag.CLEAN_CHUNK_FILE).exists():
            logger.info("Clean chunk file not found, preparing: %s", rag.RAW_INPUT_FILE)
            clean_file = rag.prepare_clean_chunks()
            logger.info("Created clean chunk file: %s", clean_file)
        else:
            clean_file = rag.CLEAN_CHUNK_FILE
            logger.info("Using existing clean chunk file: %s", clean_file)

        # build the vectorstore (this will persist Chroma DB to CHROMA_DB_PATH)
        logger.info("Building Chroma DB at: %s", rag.CHROMA_DB_PATH)
        t0 = time.time()
        vectorstore = rag.build_rag_system(clean_file)
        elapsed = time.time() - t0

        if vectorstore is None:
            logger.error("build_rag_system returned None — check logs for details")
            sys.exit(3)

        logger.info("Chroma DB build completed in %.2f seconds", elapsed)
        logger.info("Prebuild successful — Chroma DB is ready at %s", rag.CHROMA_DB_PATH)
        sys.exit(0)

    except Exception as e:
        logger.exception("Prebuild failed: %s", e)
        sys.exit(4)


if __name__ == "__main__":
    main()
