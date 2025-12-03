from fastapi import APIRouter, HTTPException
from typing import Optional
from .models import RagQueryIn, RagQueryOut, TTSIn, TTSOut
import threading
import importlib
import logging
from concurrent.futures import ThreadPoolExecutor

router = APIRouter()

# Lazy singleton for rag system
_RAG = {
    "llm": None,
    "vectorstore": None,
    "lock": threading.Lock(),
}

# internal flag set when background init started
_BG_INIT_STARTED = False
_executor = ThreadPoolExecutor(max_workers=1)


def _ensure_rag():
    if _RAG["llm"] and _RAG["vectorstore"]:
        return _RAG["llm"], _RAG["vectorstore"]

    with _RAG["lock"]:
        if _RAG["llm"] and _RAG["vectorstore"]:
            return _RAG["llm"], _RAG["vectorstore"]
        try:
            # import here so app import doesn't run rag script on startup
            import rag.rag as rag_module
        except Exception as e:
            raise RuntimeError(f"Failed to import rag module: {e}")

        try:
            llm, vectorstore = rag_module.init_rag()
        except Exception as e:
            raise RuntimeError(f"Failed to init rag system: {e}")

        _RAG["llm"] = llm
        _RAG["vectorstore"] = vectorstore
        return llm, vectorstore


def start_background_init():
    """Start background initialisation of the RAG system (non-blocking).

    Safe to call multiple times; will only schedule one background init.
    """
    global _BG_INIT_STARTED
    if _BG_INIT_STARTED:
        return
    _BG_INIT_STARTED = True

    def _task():
        try:
            logging.getLogger("uvicorn").info("Starting background RAG initialisation")
            _ensure_rag()
            logging.getLogger("uvicorn").info("Background RAG initialisation complete")
        except Exception as e:
            logging.getLogger("uvicorn").exception(f"Background RAG init failed: {e}")

    _executor.submit(_task)


@router.get('/status')
def rag_status():
    """Return readiness status for the RAG system."""
    ready = bool(_RAG["llm"] and _RAG["vectorstore"])
    return {"ready": ready}


@router.post("/query", response_model=RagQueryOut)
async def rag_query(payload: RagQueryIn):
    """Return RAG answer and metadata for a question."""
    try:
        llm, vectorstore = _ensure_rag()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    q = payload.question
    k = payload.k or int(__import__("os").environ.get("MAX_RETRIEVED_CHUNKS", 3))
    group_filter = (payload.group or "").strip()
    topic_filter = (payload.topic or "").strip()

    t_total_start = __import__("time").time()
    t_retr_start = __import__("time").time()
    docs_scores = vectorstore.similarity_search_with_score(q, k=k)
    latency_retriever = __import__("time").time() - t_retr_start
    if not docs_scores:
        return RagQueryOut(answer="", source_ids=[], groups=[], topics=[], scores=[], latency_total=latency_retriever, latency_retriever=latency_retriever)

    docs = [d for d, _ in docs_scores]
    scores = [float(s) for _, s in docs_scores]

    # Optional filter by group/topic (non-destructive; maintains backwards compatibility)
    if group_filter:
        docs = [d for d in docs if (d.metadata.get("group") or "").lower() == group_filter.lower()]
    if topic_filter:
        docs = [d for d in docs if (d.metadata.get("topic") or "").lower() == topic_filter.lower()]

    source_ids = [d.metadata.get("id") for d in docs]
    groups = [d.metadata.get("group") for d in docs]
    topics = [d.metadata.get("topic") for d in docs]

    # build context similarly to rag.chat
    t_ctx_start = __import__("time").time()
    context = "\n\n".join(
        f"[id={d.metadata.get('id')} | group={d.metadata.get('group')} | topic={d.metadata.get('topic')}] {d.page_content}"
        for d in docs
    )
    latency_context = __import__("time").time() - t_ctx_start

    # lazy-import rag module here to access BASE_PROMPT
    t_prompt_start = __import__("time").time()
    rag_module = importlib.import_module("rag.rag")
    prompt = rag_module.BASE_PROMPT.format(context=context, question=q)
    latency_prompt = __import__("time").time() - t_prompt_start

    t_llm_start = __import__("time").time()
    answer = llm.invoke(prompt)
    latency_llm = __import__("time").time() - t_llm_start
    latency_total = __import__("time").time() - t_total_start

    # Insert DB log for rag_queries
    query_id = None
    try:
        from . import db  # asyncpg pool
        pool = await db.get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO rag_queries(
                    question, answer, source_ids, groups, topics, scores,
                    latency_total, latency_retriever, latency_context, latency_prompt, latency_llm
                ) VALUES(
                    $1, $2, $3, $4, $5, $6,
                    $7, $8, $9, $10, $11
                ) RETURNING id
                """,
                q,
                answer,
                source_ids or [],
                groups or [],
                topics or [],
                scores or [],
                float(latency_total) if latency_total is not None else None,
                float(latency_retriever) if latency_retriever is not None else None,
                float(latency_context) if latency_context is not None else None,
                float(latency_prompt) if latency_prompt is not None else None,
                float(latency_llm) if latency_llm is not None else None,
            )
            if row:
                query_id = str(row[0])
    except Exception as e:
        logging.getLogger("uvicorn").warning(f"Failed to log rag_query: {e}")

    return RagQueryOut(
        query_id=query_id,
        answer=answer,
        source_ids=source_ids,
        groups=groups,
        topics=topics,
        scores=scores,
        latency_total=latency_total,
        latency_retriever=latency_retriever,
        latency_context=latency_context,
        latency_prompt=latency_prompt,
        latency_llm=latency_llm,
    )


@router.post("/tts", response_model=TTSOut)
async def tts_synthesize(payload: TTSIn):
    """Synthesize speech via Google TTS if credentials are configured.

    Returns base64 audio for easy playback by frontend clients.
    """
    try:
        # Use helper from backend.rag_mitek
        from .rag_mitek import synthesize_speech
        import base64
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TTS module import failed: {e}")

    try:
        audio_bytes, audio_path, latency = synthesize_speech(payload.text, base_filename=payload.base_filename)
        ok = bool(audio_bytes)
        error = None if ok else "TTS unavailable or credentials missing"
        audio_b64 = base64.b64encode(audio_bytes).decode("ascii") if ok else None

        # Insert DB log for rag_tts
        tts_id = None
        try:
            from . import db
            pool = await db.get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO rag_tts(query_id, text, audio_path, latency, ok, error)
                    VALUES($1, $2, $3, $4, $5, $6)
                    RETURNING id
                    """,
                    payload.query_id,
                    payload.text,
                    audio_path,
                    float(latency) if latency is not None else None,
                    ok,
                    error,
                )
                if row:
                    tts_id = str(row[0])
        except Exception as e:
            logging.getLogger("uvicorn").warning(f"Failed to log rag_tts: {e}")

        return TTSOut(ok=ok, tts_id=tts_id, audio_base64=audio_b64, audio_path=audio_path, latency=latency, error=error)
    except Exception as e:
        return TTSOut(ok=False, error=str(e))
