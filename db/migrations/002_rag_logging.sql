-- RAG interaction logging tables
-- Created: 2025-12-03

CREATE TABLE IF NOT EXISTS rag_queries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    question TEXT NOT NULL,
    answer TEXT,
    source_ids TEXT[] DEFAULT ARRAY[]::TEXT[],
    groups TEXT[] DEFAULT ARRAY[]::TEXT[],
    topics TEXT[] DEFAULT ARRAY[]::TEXT[],
    scores DOUBLE PRECISION[] DEFAULT ARRAY[]::DOUBLE PRECISION[],
    latency_total DOUBLE PRECISION,
    latency_retriever DOUBLE PRECISION,
    latency_context DOUBLE PRECISION,
    latency_prompt DOUBLE PRECISION,
    latency_llm DOUBLE PRECISION,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rag_queries_created_at ON rag_queries(created_at);

CREATE TABLE IF NOT EXISTS rag_tts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query_id UUID REFERENCES rag_queries(id) ON DELETE SET NULL,
    text TEXT NOT NULL,
    audio_path TEXT,
    latency DOUBLE PRECISION,
    ok BOOLEAN DEFAULT FALSE,
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rag_tts_query_id ON rag_tts(query_id);
CREATE INDEX IF NOT EXISTS idx_rag_tts_created_at ON rag_tts(created_at);
