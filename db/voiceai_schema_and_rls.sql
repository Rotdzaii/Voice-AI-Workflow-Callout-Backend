-- Combined VoiceAI schema + RLS (improved)
-- Created/updated: 2025-11-12
-- Note: This file contains the original schema you supplied plus an improved, more secure set of RLS policies.
-- Review the comments and adjust policies to match your gateway/auth flow (service_role, anon usage, and JWT claims).

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- === SCHEMA START ===
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

-- === RLS START ===
-- NOTE: The original RLS you provided granted wide read rights to anon/authenticated users.
-- Below I propose a tighter set of RLS policies which are safer for production.
-- Please review and adapt. I also include the original (commented) policies at the end for reference.

-- Grant usage for roles (service_role is the supabase admin key role)
GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO service_role;

-- Helper: examples of expected auth helpers (available in Supabase)
-- auth.uid() -> current user id (text), auth.role() -> role string (anon/authenticated/service_role)

-- Enable RLS on tables we want to protect
ALTER TABLE IF EXISTS public.workflows ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.workflow_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.workflow_access ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.calls ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.conversation_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.call_intents ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.call_entities ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.intents ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.entities ENABLE ROW LEVEL SECURITY;

-- Policy: workflows
DROP POLICY IF EXISTS select_workflows_on_owner_or_access ON public.workflows;
CREATE POLICY select_workflows_on_owner_or_access
    ON public.workflows
    FOR SELECT
    USING (
        -- service_role can see everything
        auth.role() = 'service_role'
        OR user_id = auth.uid()::uuid
        OR EXISTS (
            SELECT 1 FROM public.workflow_access wa
            WHERE wa.workflow_id = public.workflows.id
              AND wa.account_id = auth.uid()::uuid
        )
    );

DROP POLICY IF EXISTS insert_workflows_owner_check ON public.workflows;
CREATE POLICY insert_workflows_owner_check
    ON public.workflows
    FOR INSERT
    WITH CHECK (
        -- user creating the workflow must be the owner (user_id = auth.uid()) or service_role
        auth.role() = 'service_role' OR user_id = auth.uid()::uuid
    );

DROP POLICY IF EXISTS update_workflows_owner_or_editor ON public.workflows;
CREATE POLICY update_workflows_owner_or_editor
    ON public.workflows
    FOR UPDATE
    USING (
        auth.role() = 'service_role'
        OR user_id = auth.uid()::uuid
        OR EXISTS (
            SELECT 1 FROM public.workflow_access wa
            WHERE wa.workflow_id = public.workflows.id
              AND wa.account_id = auth.uid()::uuid
              AND wa.role IN ('owner','editor')
        )
    )
    WITH CHECK (
        auth.role() = 'service_role'
        OR user_id = auth.uid()::uuid
        OR EXISTS (
            SELECT 1 FROM public.workflow_access wa
            WHERE wa.workflow_id = public.workflows.id
              AND wa.account_id = auth.uid()::uuid
              AND wa.role IN ('owner','editor')
        )
    );

DROP POLICY IF EXISTS delete_workflows_owner_only ON public.workflows;
CREATE POLICY delete_workflows_owner_only
    ON public.workflows
    FOR DELETE
    USING (
        auth.role() = 'service_role'
        OR user_id = auth.uid()::uuid
    );

-- workflow_versions: readable if you can read the parent workflow
DROP POLICY IF EXISTS select_workflow_versions_access ON public.workflow_versions;
CREATE POLICY select_workflow_versions_access
    ON public.workflow_versions
    FOR SELECT
    USING (
        auth.role() = 'service_role'
        OR EXISTS (
            SELECT 1 FROM public.workflows w
            WHERE w.id = public.workflow_versions.workflow_id
              AND (
                   w.user_id = auth.uid()::uuid
                   OR EXISTS (
                       SELECT 1 FROM public.workflow_access wa
                       WHERE wa.workflow_id = w.id
                         AND wa.account_id = auth.uid()::uuid
                   )
              )
        )
    );

DROP POLICY IF EXISTS insert_workflow_versions_creator_check ON public.workflow_versions;
CREATE POLICY insert_workflow_versions_creator_check
    ON public.workflow_versions
    FOR INSERT
    WITH CHECK (
        auth.role() = 'service_role' OR created_by = auth.uid()::uuid
    );

-- workflow_access: allow users to list access for workflows they own or service_role
DROP POLICY IF EXISTS select_workflow_access_owner ON public.workflow_access;
CREATE POLICY select_workflow_access_owner
    ON public.workflow_access
    FOR SELECT
    USING (
        auth.role() = 'service_role'
        OR EXISTS (
            SELECT 1 FROM public.workflows w
            WHERE w.id = public.workflow_access.workflow_id
              AND w.user_id = auth.uid()::uuid
        )
    );

DROP POLICY IF EXISTS insert_workflow_access_owner ON public.workflow_access;
CREATE POLICY insert_workflow_access_owner
    ON public.workflow_access
    FOR INSERT
    WITH CHECK (
        auth.role() = 'service_role'
        OR EXISTS (
            SELECT 1 FROM public.workflows w
            WHERE w.id = public.workflow_access.workflow_id
              AND w.user_id = auth.uid()::uuid
        )
    );

-- calls: allow service role or users who can access the workflow to insert/select/update
DROP POLICY IF EXISTS select_calls_workflow_access ON public.calls;
CREATE POLICY select_calls_workflow_access
    ON public.calls
    FOR SELECT
    USING (
        auth.role() = 'service_role'
        OR EXISTS (
            SELECT 1 FROM public.workflows w
            WHERE w.id = public.calls.workflow_id
              AND (
                   w.user_id = auth.uid()::uuid
                   OR EXISTS (
                       SELECT 1 FROM public.workflow_access wa
                       WHERE wa.workflow_id = w.id
                         AND wa.account_id = auth.uid()::uuid
                   )
              )
        )
    );

DROP POLICY IF EXISTS insert_calls_with_workflow_access_check ON public.calls;
CREATE POLICY insert_calls_with_workflow_access_check
    ON public.calls
    FOR INSERT
    WITH CHECK (
        -- service_role or caller must have access to the workflow
        auth.role() = 'service_role'
        OR EXISTS (
            SELECT 1 FROM public.workflows w
            WHERE w.id = public.calls.workflow_id
              AND (
                   w.user_id = auth.uid()::uuid
                   OR EXISTS (
                       SELECT 1 FROM public.workflow_access wa
                       WHERE wa.workflow_id = w.id
                         AND wa.account_id = auth.uid()::uuid
                   )
              )
        )
    );

DROP POLICY IF EXISTS update_calls_workflow_access ON public.calls;
CREATE POLICY update_calls_workflow_access
    ON public.calls
    FOR UPDATE
    USING (
        auth.role() = 'service_role'
        OR EXISTS (
            SELECT 1 FROM public.workflows w
            WHERE w.id = public.calls.workflow_id
              AND (
                   w.user_id = auth.uid()::uuid
                   OR EXISTS (
                       SELECT 1 FROM public.workflow_access wa
                       WHERE wa.workflow_id = w.id
                         AND wa.account_id = auth.uid()::uuid
                         AND wa.role IN ('owner','editor')
                   )
              )
        )
    )
    WITH CHECK (
        auth.role() = 'service_role'
        OR EXISTS (
            SELECT 1 FROM public.workflows w
            WHERE w.id = public.calls.workflow_id
              AND (
                   w.user_id = auth.uid()::uuid
                   OR EXISTS (
                       SELECT 1 FROM public.workflow_access wa
                       WHERE wa.workflow_id = w.id
                         AND wa.account_id = auth.uid()::uuid
                         AND wa.role IN ('owner','editor')
                   )
              )
        )
    );

-- conversation_logs: similar to calls - allow insert by service_role or authorized caller
DROP POLICY IF EXISTS select_convlogs_calls_access ON public.conversation_logs;
CREATE POLICY select_convlogs_calls_access
    ON public.conversation_logs
    FOR SELECT
    USING (
        auth.role() = 'service_role'
        OR EXISTS (
            SELECT 1 FROM public.calls c
            JOIN public.workflows w ON w.id = c.workflow_id
            WHERE c.id = public.conversation_logs.call_id
              AND (
                   w.user_id = auth.uid()::uuid
                   OR EXISTS (
                       SELECT 1 FROM public.workflow_access wa
                       WHERE wa.workflow_id = w.id
                         AND wa.account_id = auth.uid()::uuid
                   )
              )
        )
    );

DROP POLICY IF EXISTS insert_convlogs_calls_access_check ON public.conversation_logs;
CREATE POLICY insert_convlogs_calls_access_check
    ON public.conversation_logs
    FOR INSERT
    WITH CHECK (
        auth.role() = 'service_role'
        OR EXISTS (
            SELECT 1 FROM public.calls c
            JOIN public.workflows w ON w.id = c.workflow_id
            WHERE c.id = public.conversation_logs.call_id
              AND (
                   w.user_id = auth.uid()::uuid
                   OR EXISTS (
                       SELECT 1 FROM public.workflow_access wa
                       WHERE wa.workflow_id = w.id
                         AND wa.account_id = auth.uid()::uuid
                   )
              )
        )
    );

-- intents/entities: generally readable by authenticated users but writeable only by editors/admins
DROP POLICY IF EXISTS select_intents_all_authenticated ON public.intents;
CREATE POLICY select_intents_all_authenticated
    ON public.intents
    FOR SELECT
    USING (
        auth.role() IN ('authenticated','service_role')
    );

DROP POLICY IF EXISTS insert_intents_admin_only ON public.intents;
CREATE POLICY insert_intents_admin_only
    ON public.intents
    FOR INSERT
    WITH CHECK (
        auth.role() = 'service_role' OR (
            -- optionally allow certain users by checking accounts.role
            EXISTS (
                SELECT 1 FROM public.accounts a
                WHERE a.id = auth.uid()::uuid AND a.role IN ('admin')
            )
        )
    );

DROP POLICY IF EXISTS select_entities_all_authenticated ON public.entities;
CREATE POLICY select_entities_all_authenticated
    ON public.entities
    FOR SELECT
    USING (
        auth.role() IN ('authenticated','service_role')
    );

DROP POLICY IF EXISTS insert_entities_admin_only ON public.entities;
CREATE POLICY insert_entities_admin_only
    ON public.entities
    FOR INSERT
    WITH CHECK (
        auth.role() = 'service_role' OR (
            EXISTS (
                SELECT 1 FROM public.accounts a
                WHERE a.id = auth.uid()::uuid AND a.role IN ('admin')
            )
        )
    );

-- call_intents/call_entities: restrict to calls that the user can access (via call -> workflow)
DROP POLICY IF EXISTS select_call_intents_access ON public.call_intents;
CREATE POLICY select_call_intents_access
    ON public.call_intents
    FOR SELECT
    USING (
        auth.role() = 'service_role'
        OR EXISTS (
            SELECT 1 FROM public.calls c
            JOIN public.workflows w ON w.id = c.workflow_id
            WHERE c.id = public.call_intents.call_id
              AND (
                   w.user_id = auth.uid()::uuid
                   OR EXISTS (
                       SELECT 1 FROM public.workflow_access wa
                       WHERE wa.workflow_id = w.id
                         AND wa.account_id = auth.uid()::uuid
                   )
              )
        )
    );

DROP POLICY IF EXISTS insert_call_intents_check ON public.call_intents;
CREATE POLICY insert_call_intents_check
    ON public.call_intents
    FOR INSERT
    WITH CHECK (
        auth.role() = 'service_role'
        OR EXISTS (
            SELECT 1 FROM public.calls c
            JOIN public.workflows w ON w.id = c.workflow_id
            WHERE c.id = public.call_intents.call_id
              AND (
                   w.user_id = auth.uid()::uuid
                   OR EXISTS (
                       SELECT 1 FROM public.workflow_access wa
                       WHERE wa.workflow_id = w.id
                         AND wa.account_id = auth.uid()::uuid
                   )
              )
        )
    );

DROP POLICY IF EXISTS select_call_entities_access ON public.call_entities;
CREATE POLICY select_call_entities_access
    ON public.call_entities
    FOR SELECT
    USING (
        auth.role() = 'service_role'
        OR EXISTS (
            SELECT 1 FROM public.calls c
            JOIN public.workflows w ON w.id = c.workflow_id
            WHERE c.id = public.call_entities.call_id
              AND (
                   w.user_id = auth.uid()::uuid
                   OR EXISTS (
                       SELECT 1 FROM public.workflow_access wa
                       WHERE wa.workflow_id = w.id
                         AND wa.account_id = auth.uid()::uuid
                   )
              )
        )
    );

DROP POLICY IF EXISTS insert_call_entities_check ON public.call_entities;
CREATE POLICY insert_call_entities_check
    ON public.call_entities
    FOR INSERT
    WITH CHECK (
        auth.role() = 'service_role'
        OR EXISTS (
            SELECT 1 FROM public.calls c
            JOIN public.workflows w ON w.id = c.workflow_id
            WHERE c.id = public.call_entities.call_id
              AND (
                   w.user_id = auth.uid()::uuid
                   OR EXISTS (
                       SELECT 1 FROM public.workflow_access wa
                       WHERE wa.workflow_id = w.id
                         AND wa.account_id = auth.uid()::uuid
                   )
              )
        )
    );

-- reports: allow only service_role and owners (or editors/viewers as needed)
DROP POLICY IF EXISTS select_reports_owner_or_service ON public.reports;
CREATE POLICY select_reports_owner_or_service
    ON public.reports
    FOR SELECT
    USING (
        auth.role() = 'service_role'
        OR EXISTS (
            SELECT 1 FROM public.workflows w
            WHERE w.id = public.reports.workflow_id
              AND (
                   w.user_id = auth.uid()::uuid
                   OR EXISTS (
                       SELECT 1 FROM public.workflow_access wa
                       WHERE wa.workflow_id = w.id
                         AND wa.account_id = auth.uid()::uuid
                   )
              )
        )
    );

-- === END RLS ===

-- === ORIGINAL RLS (for reference) ===
-- The policies below are the original snippet you provided. They are left commented for reference.
-- Uncomment to use them instead of the tightened policies above.

-- GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
-- GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role;
-- GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO service_role;
-- GRANT SELECT ON TABLE public.workflows, public.intents, public.entities, public.calls, public.conversation_logs TO anon, authenticated;
-- GRANT INSERT ON TABLE public.conversation_logs TO anon, authenticated;
-- GRANT INSERT ON TABLE public.calls TO anon, authenticated;

-- ALTER TABLE IF EXISTS public.workflows ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE IF EXISTS public.conversation_logs ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE IF EXISTS public.calls ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE IF EXISTS public.intents ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE IF EXISTS public.entities ENABLE ROW LEVEL SECURITY;

-- DROP POLICY IF EXISTS anon_select_workflows ON public.workflows;
-- CREATE POLICY anon_select_workflows
--     ON public.workflows FOR SELECT
--     USING (auth.role() = 'anon');

-- DROP POLICY IF EXISTS anon_select_intents ON public.intents;
-- CREATE POLICY anon_select_intents
--     ON public.intents FOR SELECT
--     USING (auth.role() = 'anon');

-- DROP POLICY IF EXISTS anon_select_entities ON public.entities;
-- CREATE POLICY anon_select_entities
--     ON public.entities FOR SELECT
--     USING (auth.role() = 'anon');

-- DROP POLICY IF EXISTS anon_select_calls ON public.calls;
-- CREATE POLICY anon_select_calls
--     ON public.calls FOR SELECT
--     USING (auth.role() = 'anon');

-- DROP POLICY IF EXISTS anon_insert_calls ON public.calls;
-- CREATE POLICY anon_insert_calls
--     ON public.calls FOR INSERT
--     WITH CHECK (auth.role() = 'anon');

-- DROP POLICY IF EXISTS anon_select_conversation_logs ON public.conversation_logs;
-- CREATE POLICY anon_select_conversation_logs
--     ON public.conversation_logs FOR SELECT
--     USING (auth.role() = 'anon');

-- DROP POLICY IF EXISTS anon_insert_conversation_logs ON public.conversation_logs;
-- CREATE POLICY anon_insert_conversation_logs
--     ON public.conversation_logs FOR INSERT
--     WITH CHECK (auth.role() = 'anon');

-- End of file
