VoiceAI - Workspace (MVP scaffold)

This workspace contains a minimal backend scaffold and a Python virtual environment setup.

Quick start (Windows PowerShell):

1) Create venv (if not already created by the scaffold step):
   cd c:\Users\Admin\MyProject\va
   python -m venv .venv

2) Activate venv:
   .\.venv\Scripts\Activate.ps1

3) Update pip and install deps:
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt

4) Configure environment:
   - Copy .env.example to .env and fill values (especially DATABASE_URL, SUPABASE_JWT_SECRET).
   - The backend loads .env automatically via backend/config.py.

5) Run the backend (from project root):
   .\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

Open http://localhost:8000/health to check, and http://localhost:8000/docs for Swagger when server is running.

Notes:
- The repository currently includes a SQL schema file at `db/voiceai_schema_and_rls.sql`.
- Next steps: implement DB connection, auth, and endpoints (/call/start, /call/reply)."

Local Postgres with Docker (recommended for dev)

1) Start Postgres via Docker Compose (project root):

   docker compose up -d

   This starts Postgres 15 on localhost:5432 with credentials: postgres/postgres and DB name `voiceai`.

2) Initialize DB schema (from project root):

   .\.venv\Scripts\python.exe scripts\init_db.py

   This reads `db/voiceai_schema_and_rls.sql` and applies it to the DB using asyncpg.

Notes:
- If you don't have Docker, you can use a local Postgres instance and set `DATABASE_URL` environment variable.
- The default `DATABASE_URL` used by the code is `postgresql://postgres:postgres@127.0.0.1:5432/voiceai`.

Supabase (cloud) - quick apply

1) In your Supabase project, open "SQL" → "New query" and paste the contents of `db/voiceai_schema_combined.sql` (your original combined schema + RLS) then Run.

   This will create the tables, views and the original (open) RLS policies (anon read/insert as in PRD). Use this only for demo/testing; for production consider tightening policies.

2) In your Supabase Project Settings → API, copy the "Config" → "DB connection string (URI)" and set it as an environment variable for the backend process:

   - DATABASE_URL=postgresql://...   (the connection string)
   - SUPABASE_JWT_SECRET=<your JWT secret from Supabase settings>

3) Option A: set env vars for the current shell session (PowerShell):

   $env:DATABASE_URL = "postgresql://..."
   $env:SUPABASE_JWT_SECRET = "<your_supabase_jwt_secret>"
   .\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

   Option B: put these into .env, which is auto-loaded.

4) Use Supabase Auth in your frontend to sign in users. Backend will validate Supabase JWT tokens using `SUPABASE_JWT_SECRET`.

Notes on RLS: the `db/voiceai_schema_combined.sql` file is your original open policy set. If you later want strong data isolation, use the tightened RLS file `db/voiceai_schema_and_rls.sql` instead.

Run tests

From project root (venv activated):

   .\.venv\Scripts\python.exe -m pytest -q

We currently include lightweight smoke tests for /health and docs endpoints. These do not require a live database.

Railway deployment (FastAPI)

Prereqs:
- Railway account and CLI installed (optional; you can also deploy via Dashboard).

Included files:
- `Procfile` → defines the web process for Uvicorn using the PORT provided by Railway.
- `railway.json` → optional metadata for deployment (healthcheck, restart policy). Schema is informal; you can also configure via Dashboard.

Steps (CLI or Dashboard):
1) Create a new Railway project and attach this repo.
2) Ensure `Python` is detected (Nixpacks will install from `requirements.txt`).
3) Add environment variables in Railway Settings:
   - `DATABASE_URL` (Supabase connection string)
   - `SUPABASE_JWT_SECRET` (from Supabase)
   - `SUPABASE_URL`, `SUPABASE_ANON_KEY` (để dùng Supabase SDK realtime)
   - `SUPABASE_USE_SDK=true` (bật SDK đường realtime/REST)
4) Deploy. Railway will run `web: uvicorn backend.main:app --host 0.0.0.0 --port ${PORT}` from `Procfile`.
5) Verify:
   - `GET /health` returns `{ "status": "ok" }`
   - `GET /docs` shows Swagger UI
   - `GET /debug/db_ping` returns `{ ok: true }` if DB reachable

Supabase connectivity check (optional, local)

With venv activated and `DATABASE_URL` set, run:

   .\.venv\Scripts\python.exe scripts\check_db.py

This prints DB version and lists some tables to confirm connectivity.

---

Advanced Features & Configuration

This backend now includes optional advanced modules. All are feature-flagged or gracefully degrade if not configured.

Environment Variables (summary)

Required (minimum to run against Supabase):
- DATABASE_URL = postgresql://... (Supabase connection string) OR local Postgres URL
- SUPABASE_JWT_SECRET = JWT secret from Supabase settings (used to validate user tokens)

Realtime & REST via Supabase SDK (optional):
- SUPABASE_URL = https://xyzcompany.supabase.co
- SUPABASE_ANON_KEY = anon public key (for lightweight REST/realtime)
- SUPABASE_SERVICE_ROLE_KEY = service role (only if you plan backend-side privileged inserts via SDK; keep secret!)
- SUPABASE_USE_SDK = true (enable SDK path for realtime conversation logs)

NLU Engine selection:
- NLU_ENGINE = simple | phobert (default: simple)

Deeppavlov-Agent integration:
- DEEPPAVLOV_URL = http(s)://host:port/ (root URL of external agent service)

Asterisk AMI originate (telephony demo):
- ASTERISK_HOST
- ASTERISK_PORT (default 5038)
- ASTERISK_USERNAME
- ASTERISK_SECRET
- ASTERISK_CONTEXT (dialplan context)
- ASTERISK_EXTENSION (default extension if not provided in request)
- ASTERISK_CALLERID (optional default caller ID)

Optional performance / GPU NLP:
- Set NLU_ENGINE=phobert and install optional deps (see below). Falls back automatically to simple if model load fails.

PhoBERT NLU (optional)

1) Install heavy NLP dependencies (PowerShell):
   python -m pip install -r requirements-optional.txt

2) Set environment variable:
   $env:NLU_ENGINE = "phobert"  # or put in .env

3) (First run) Model weights vinai/phobert-base-v2 will be downloaded. If this fails (no GPU / memory), the code logs a warning and reverts to simple NLU.

Endpoints exposed:

Auth:
- POST /auth/register → create user (demo scope; adjust for production)
- POST /auth/token → login (returns JWT)

Workflows:
- POST /workflows → create workflow
- GET /workflows → list workflows
- GET /workflows/{id} → get one
- PUT /workflows/{id} → update
- DELETE /workflows/{id} → delete

Calls & Logs:
- POST /call/start → create call session tied to workflow
- POST /call/reply → append a conversation log entry
- GET /calls/{call_id}/logs → fetch all logs (snapshot)
- WS /ws/calls/{call_id}/logs → realtime stream (initial snapshot + new entries)

NLU / Conversation:
- POST /nlu/parse → run selected NLU engine
- POST /conversation/next → simple policy-based next turn reply
- POST /conversation/agent → proxy to Deeppavlov-Agent (if configured) else fallback reply

Telephony:
- POST /call/originate → originate a call via Asterisk AMI (requires AMI env vars). Returns { ok: true/false, message }

Intents & Entities:
- GET /intents
- GET /entities

Realtime Conversation Logs

WebSocket: WS /ws/calls/{call_id}/logs
Behavior:
1) On connect: sends a snapshot of existing logs.
2) If SUPABASE_USE_SDK=true and SDK env vars present: subscribes to realtime channel and pushes new rows immediately.
3) Else: falls back to lightweight internal append mechanism (or polling) so tests and local dev still work.

Deeppavlov-Agent Integration

If DEEPPAVLOV_URL is set, /conversation/agent forwards user text:
  { "call_id": "uuid", "text": "Xin chào" }
Backend awaits JSON response structure: { "reply": "..." } (or similar) and returns standardized payload. If unreachable, returns a graceful fallback message.

Asterisk AMI Originate Demo

Call example (PowerShell Invoke-RestMethod):
  $body = @{ channel="SIP/100" exten="100" context="default" priority=1 caller_id="VoiceAI" variables=@{ foo="bar" } } | ConvertTo-Json
  Invoke-RestMethod -Uri http://localhost:8000/call/originate -Method Post -Body $body -ContentType 'application/json'

Security note: AMI credentials are sensitive; never commit them. Consider migrating to ARI for richer control & events later.

Testing

Basic suite (no DB needed for most):
   python -m pytest -q

To include DB-dependent validations, ensure DATABASE_URL points to a live instance with applied schema (scripts/init_db.py or Supabase already set up).

Deployment (Railway) – Advanced Notes

Add optional vars as needed:
- SUPABASE_USE_SDK=true for realtime stream
- NLU_ENGINE=phobert only if requirements-optional installed in build (add a second deploy or custom Nixpacks phase if GPU needed)
- DEEPPAVLOV_URL for external agent
- Asterisk vars for telephony

Scaling Tips:
- Enable connection pooling (pgBouncer) on Supabase for high concurrency.
- Consider moving conversation state out of memory into Redis or Postgres for multi-instance scaling.
- Tighten RLS policies before production (swap to the secured RLS file; adapt policies to user_id scoping).

Next Hardening Steps (suggested):
1) Replace open RLS with restrictive per-user policies.
2) Add structured logging & tracing (OpenTelemetry) for call sessions.
3) Introduce ARI or a robust telephony library for call lifecycle events.
4) Expand NLU to custom intent/entity training pipeline with evaluation tests.
5) Add load tests (locust / k6) before production launch.

---