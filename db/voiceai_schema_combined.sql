-- Combined schema + RLS policies for VoiceAI (apply in one run)
-- Created/updated: 2025-11-05

-- === SCHEMA START (from supabase_schema.sql) ===
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT DEFAULT 'user',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_accounts_email ON accounts(email);

CREATE TABLE IF NOT EXISTS workflows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES accounts(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    workflow_json JSONB,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_workflows_user ON workflows(user_id);
CREATE INDEX IF NOT EXISTS idx_workflows_created_at ON workflows(created_at);

CREATE TABLE IF NOT EXISTS workflow_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id UUID REFERENCES workflows(id) ON DELETE CASCADE,
    version_number INT NOT NULL,
    workflow_json JSONB NOT NULL,
    changelog TEXT,
    created_by UUID REFERENCES accounts(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_workflow_versions_wid ON workflow_versions(workflow_id);

CREATE TABLE IF NOT EXISTS workflow_access (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id UUID REFERENCES workflows(id) ON DELETE CASCADE,
    account_id UUID REFERENCES accounts(id) ON DELETE CASCADE,
    role TEXT CHECK (role IN ('owner', 'editor', 'viewer')) DEFAULT 'viewer',
    granted_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_workflow_access_wid ON workflow_access(workflow_id);

CREATE TABLE IF NOT EXISTS workflow_tags (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id UUID REFERENCES workflows(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_workflow_tags_wid ON workflow_tags(workflow_id);

CREATE TABLE IF NOT EXISTS calls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id UUID REFERENCES workflows(id) ON DELETE CASCADE,
    customer_phone TEXT,
    status TEXT DEFAULT 'in_progress',
    start_time TIMESTAMPTZ,
    end_time TIMESTAMPTZ,
    duration DOUBLE PRECISION,
    last_intent TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_calls_wid ON calls(workflow_id);
CREATE INDEX IF NOT EXISTS idx_calls_start_time ON calls(start_time);

CREATE TABLE IF NOT EXISTS conversation_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    call_id UUID REFERENCES calls(id) ON DELETE CASCADE,
    speaker TEXT,
    text TEXT,
    intent TEXT,
    confidence FLOAT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_convlogs_callid ON conversation_logs(call_id);
CREATE INDEX IF NOT EXISTS idx_convlogs_created_at ON conversation_logs(created_at);

CREATE TABLE IF NOT EXISTS call_intents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    call_id UUID REFERENCES calls(id) ON DELETE CASCADE,
    intent_name TEXT,
    count INT DEFAULT 0,
    accuracy FLOAT DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_call_intents_callid ON call_intents(call_id);

CREATE TABLE IF NOT EXISTS call_entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    call_id UUID REFERENCES calls(id) ON DELETE CASCADE,
    entity_name TEXT,
    value TEXT
);
CREATE INDEX IF NOT EXISTS idx_call_entities_callid ON call_entities(call_id);

CREATE TABLE IF NOT EXISTS reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id UUID REFERENCES workflows(id) ON DELETE CASCADE,
    total_calls INT DEFAULT 0,
    success_rate FLOAT DEFAULT 0.0,
    avg_duration FLOAT DEFAULT 0.0,
    positive_intent_rate FLOAT DEFAULT 0.0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_reports_wid ON reports(workflow_id);

CREATE TABLE IF NOT EXISTS intents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    category TEXT,
    example_phrases JSONB,
    training_data JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_intents_name ON intents(name);

CREATE TABLE IF NOT EXISTS entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    type TEXT,
    regex_pattern TEXT,
    example_values JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);

CREATE TABLE IF NOT EXISTS intent_entities (
    intent_id UUID REFERENCES intents(id) ON DELETE CASCADE,
    entity_id UUID REFERENCES entities(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (intent_id, entity_id)
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'fk_call_intents_intents'
    ) THEN
        ALTER TABLE IF EXISTS call_intents
        ADD CONSTRAINT fk_call_intents_intents
        FOREIGN KEY (intent_name)
        REFERENCES intents(name)
        ON UPDATE CASCADE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'fk_call_entities_entities'
    ) THEN
        ALTER TABLE IF EXISTS call_entities
        ADD CONSTRAINT fk_call_entities_entities
        FOREIGN KEY (entity_name)
        REFERENCES entities(name)
        ON UPDATE CASCADE;
    END IF;
END $$;

CREATE OR REPLACE VIEW v_call_summary AS
SELECT
  c.id AS call_id,
  w.name AS workflow_name,
  c.customer_phone,
  c.status,
  c.duration,
  ci.intent_name,
  ce.entity_name,
  ce.value
FROM calls c
LEFT JOIN workflows w ON c.workflow_id = w.id
LEFT JOIN call_intents ci ON ci.call_id = c.id
LEFT JOIN call_entities ce ON ce.call_id = c.id;

CREATE OR REPLACE VIEW v_workflow_summary AS
SELECT 
  w.id AS workflow_id,
  w.name,
  w.status,
  COUNT(DISTINCT c.id) AS total_calls,
  MAX(wv.version_number) AS latest_version,
  array_remove(array_agg(DISTINCT t.tag), NULL) AS tags
FROM workflows w
LEFT JOIN calls c ON w.id = c.workflow_id
LEFT JOIN workflow_versions wv ON w.id = wv.workflow_id
LEFT JOIN workflow_tags t ON w.id = t.workflow_id
GROUP BY w.id, w.name, w.status;
-- === SCHEMA END ===

-- === RLS START (from supabase_rls.sql) ===
-- Ensure roles have base privileges (RLS still applies afterward)
GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO service_role;
GRANT SELECT ON TABLE public.workflows, public.intents, public.entities, public.calls, public.conversation_logs TO anon, authenticated;
GRANT INSERT ON TABLE public.conversation_logs TO anon, authenticated;
GRANT INSERT ON TABLE public.calls TO anon, authenticated;

ALTER TABLE IF EXISTS public.workflows ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.conversation_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.calls ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.intents ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.entities ENABLE ROW LEVEL SECURITY;

-- Policies (use drop+create to be idempotent on Postgres 15)
DROP POLICY IF EXISTS anon_select_workflows ON public.workflows;
CREATE POLICY anon_select_workflows
    ON public.workflows FOR SELECT
    USING (auth.role() = 'anon');

DROP POLICY IF EXISTS anon_select_intents ON public.intents;
CREATE POLICY anon_select_intents
    ON public.intents FOR SELECT
    USING (auth.role() = 'anon');

DROP POLICY IF EXISTS anon_select_entities ON public.entities;
CREATE POLICY anon_select_entities
    ON public.entities FOR SELECT
    USING (auth.role() = 'anon');

DROP POLICY IF EXISTS anon_select_calls ON public.calls;
CREATE POLICY anon_select_calls
    ON public.calls FOR SELECT
    USING (auth.role() = 'anon');

DROP POLICY IF EXISTS anon_insert_calls ON public.calls;
CREATE POLICY anon_insert_calls
    ON public.calls FOR INSERT
    WITH CHECK (auth.role() = 'anon');

DROP POLICY IF EXISTS anon_select_conversation_logs ON public.conversation_logs;
CREATE POLICY anon_select_conversation_logs
    ON public.conversation_logs FOR SELECT
    USING (auth.role() = 'anon');

DROP POLICY IF EXISTS anon_insert_conversation_logs ON public.conversation_logs;
CREATE POLICY anon_insert_conversation_logs
    ON public.conversation_logs FOR INSERT
    WITH CHECK (auth.role() = 'anon');
-- === RLS END ===

-- End of combined file
