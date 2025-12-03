-- RAG cache for speeding up repeated queries
-- Created: 2025-12-03

CREATE TABLE IF NOT EXISTS rag_cache (
    key TEXT PRIMARY KEY,
    question TEXT,
    group_filter TEXT,
    topic_filter TEXT,
    k INT,
    answer TEXT,
    source_ids TEXT[] DEFAULT ARRAY[]::TEXT[],
    groups TEXT[] DEFAULT ARRAY[]::TEXT[],
    topics TEXT[] DEFAULT ARRAY[]::TEXT[],
    scores DOUBLE PRECISION[] DEFAULT ARRAY[]::DOUBLE PRECISION[],
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expire_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_rag_cache_expire_at ON rag_cache(expire_at);
