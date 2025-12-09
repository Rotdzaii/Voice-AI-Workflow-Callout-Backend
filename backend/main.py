import asyncio
import json
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import Body, Depends, FastAPI, File, HTTPException, Request, Response, UploadFile, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import auth, db, models, oauth, rag_api, supabase_client, voice_stream
from .asterisk import originate
from .config import USE_SUPABASE_SDK, env_str
from .conversation import process_turn
from .deeppavlov_client import get_agent
from .logging_setup import calls_started, conversation_logs_inserted, get_registry, stt_requests
from .models import CallReplyIn, CallStartIn, ConversationAgentIn, ConversationIn, ConversationOut, NLUParseIn, NLUParseOut
from .nlu import get_nlu
from .routers import workflows as workflows_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown handlers."""
    # Startup
    try:
        await db.get_pool()
    except Exception:
        pass  # Keep app running even if DB is not reachable at boot
    
    # Non-blocking background RAG init to make RAG ready early
    try:
        rag_api.start_background_init()
    except Exception:
        pass
    
    yield
    
    # Shutdown
    try:
        await db.close_pool()
    except Exception:
        pass


app = FastAPI(title="VoiceAI - Backend (MVP)", lifespan=lifespan)

# Register RAG router so /rag endpoints stay available in all builds
app.include_router(rag_api.router, prefix="/rag")

# Enable CORS for frontend integration. Always permit local dev hosts and honor
# ALLOWED_ORIGINS when provided, while keeping a permissive wildcard fallback for demos.
_default_allowed = ["http://localhost:5173", "http://localhost:3000", "*"]
raw_origins = env_str("ALLOWED_ORIGINS", ",".join(_default_allowed)) or ",".join(_default_allowed)
configured_origins = []
allow_wildcard = False
for origin in raw_origins.split(","):
    entry = origin.strip()
    if not entry:
        continue
    if entry == "*":
        allow_wildcard = True
        continue
    configured_origins.append(entry)

if not configured_origins:
    configured_origins = [o for o in _default_allowed if o != "*"]

# Allow file:// pages (Origin header is literal "null") by default so QA can open
# the static chat_test.html without running a frontend dev server.
if "null" not in configured_origins:
    configured_origins.append("null")

app.add_middleware(
    CORSMiddleware,
    allow_origins=configured_origins,
    allow_origin_regex=".*" if allow_wildcard else None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(workflows_router.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/chat", response_class=HTMLResponse)
async def chat_test():
    """Serve simple chat test UI"""
    try:
        with open("frontend/chat_test.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except Exception:
        return HTMLResponse(content="<h1>Chat test not found</h1>", status_code=404)


@app.get("/voice", response_class=HTMLResponse)
async def voice_test():
    """Serve voice AI demo UI"""
    try:
        with open("frontend/voice_test.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except Exception:
        return HTMLResponse(content="<h1>Voice test not found</h1>", status_code=404)


@app.get("/voice_ai", response_class=HTMLResponse)
async def voice_ai():
    """Serve modern voice AI UI"""
    try:
        with open("frontend/voice_ai.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except Exception:
        return HTMLResponse(content="<h1>Voice AI not found</h1>", status_code=404)


@app.get("/stt_test", response_class=HTMLResponse)
async def stt_test():
    """Serve STT testing UI"""
    try:
        with open("frontend/stt_test.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except Exception:
        return HTMLResponse(content="<h1>STT test not found</h1>", status_code=404)


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")


@app.post("/auth/token", response_model=models.Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    # minimal: verify user exists in accounts table
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id, email, password_hash, role FROM accounts WHERE email=$1", form_data.username)
        if not row:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
        if not auth.verify_password(form_data.password, row["password_hash"]):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
        token = auth.create_access_token({"sub": str(row["id"]), "role": row["role"]})
        return {"access_token": token, "token_type": "bearer"}


async def get_current_user(token: str = Depends(oauth2_scheme)):
    payload = auth.decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user_id = payload.get("sub")
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id, email, role FROM accounts WHERE id=$1", user_id)
        if not row:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
        return {"id": str(row["id"]), "email": row["email"], "role": row["role"]}


# -------------------- OAuth: Google --------------------

@app.get("/auth/oauth/google/start")
async def oauth_google_start():
    try:
        url, state, nonce, code_verifier = oauth.google_auth_url()
        resp = RedirectResponse(url)
        oauth.set_state_cookie(resp, state)
        oauth.set_nonce_cookie(resp, nonce)
        oauth.set_code_verifier_cookie(resp, code_verifier)
        return resp
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)


@app.get("/auth/oauth/google/callback")
async def oauth_google_callback(request: Request, code: str | None = None, state: str | None = None, nonce: str | None = None, web_nonce: str | None = None):
    if not code or not oauth.verify_state(state, "google"):
        return HTMLResponse(_callback_html_error("google", "missing code/state"), status_code=400)
    # Verify cookies binding
    state_cookie = request.cookies.get(oauth.STATE_COOKIE_NAME)
    nonce_cookie = request.cookies.get(oauth.NONCE_COOKIE_NAME)
    if not oauth.verify_cookies(state_cookie, nonce_cookie, state, nonce):
        return HTMLResponse(_callback_html_error("google", "failed cookie/state binding"), status_code=400)
    code_verifier_signed = request.cookies.get(oauth.CODE_VERIFIER_COOKIE_NAME)
    code_verifier = oauth.extract_signed_code_verifier(code_verifier_signed)
    try:
        token_payload = await oauth.google_exchange_code(code, code_verifier)
        access_token = token_payload.get("access_token")
        if not access_token:
            raise RuntimeError("no access_token from Google")
        profile = await oauth.google_userinfo(access_token)
        email = profile.get("email")
        if not email:
            raise RuntimeError("no email from Google userinfo")
        pool = await db.get_pool()
        async with pool.acquire() as conn:
            uid, role = await oauth.get_or_create_account_by_email(conn, email)
        app_token = auth.create_access_token({
            "sub": uid,
            "email": email,
            "role": role or "user",
            "provider": "google",
            "name": profile.get("name"),
            "picture": profile.get("picture"),
        })
        # Optional: ensure web_nonce (from frontend) matches provider nonce
        if web_nonce and nonce and web_nonce != nonce:
            return HTMLResponse(_callback_html_error("google", "web_nonce mismatch"), status_code=400)
        return HTMLResponse(_callback_html_success("google", app_token, web_nonce))
    except Exception as e:
        return HTMLResponse(_callback_html_error("google", str(e)), status_code=500)


# -------------------- OAuth: GitHub --------------------

@app.get("/auth/oauth/github/start")
async def oauth_github_start():
    try:
        url, state = oauth.github_auth_url()
        resp = RedirectResponse(url)
        oauth.set_state_cookie(resp, state)
        return resp
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)


@app.get("/auth/oauth/github/callback")
async def oauth_github_callback(request: Request, code: str | None = None, state: str | None = None, web_nonce: str | None = None):
    if not code or not oauth.verify_state(state, "github"):
        return HTMLResponse(_callback_html_error("github", "missing code/state"), status_code=400)
    # Verify cookie binding
    state_cookie = request.cookies.get(oauth.STATE_COOKIE_NAME)
    if not oauth.verify_cookies(state_cookie, None, state, None):
        return HTMLResponse(_callback_html_error("github", "failed cookie/state binding"), status_code=400)
    try:
        token_payload = await oauth.github_exchange_code(code)
        access_token = token_payload.get("access_token")
        if not access_token:
            raise RuntimeError("no access_token from GitHub")
        profile = await oauth.github_userinfo(access_token)
        email = profile.get("email")
        if not email:
            raise RuntimeError("no email from GitHub userinfo")
        pool = await db.get_pool()
        async with pool.acquire() as conn:
            uid, role = await oauth.get_or_create_account_by_email(conn, email)
        app_token = auth.create_access_token({
            "sub": uid,
            "email": email,
            "role": role or "user",
            "provider": "github",
            "name": profile.get("name") or profile.get("login"),
            "picture": profile.get("avatar_url"),
        })
        return HTMLResponse(_callback_html_success("github", app_token, web_nonce))
    except Exception as e:
        return HTMLResponse(_callback_html_error("github", str(e)), status_code=500)


@app.post("/auth/register", response_model=models.UserOut)
async def register(user: models.UserCreate):
    """Register a new account.

    If `SUPABASE_SERVICE_ROLE_KEY` is configured, use Supabase REST API to insert the account
    (works even if direct DB TCP connections are blocked). Otherwise fall back to direct DB insert.
    """
    import os
    try:
        hashed = auth.get_password_hash(user.password)

        supabase_url = os.environ.get("SUPABASE_URL")
        supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if supabase_url and supabase_key:
            # Use Supabase REST API to insert account (service_role key bypasses RLS)
            try:
                import requests
                headers = {
                    "apikey": supabase_key,
                    "Authorization": f"Bearer {supabase_key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=representation",
                }
                payload = {"email": user.email, "password_hash": hashed, "role": "user"}
                resp = requests.post(f"{supabase_url}/rest/v1/accounts", json=payload, headers=headers, timeout=10)
                if resp.status_code in (200, 201):
                    data = resp.json()
                    # Supabase returns an array of created rows when return=representation
                    if isinstance(data, list) and data:
                        row = data[0]
                        return {"id": str(row.get("id")), "email": row.get("email"), "role": row.get("role")}
                    else:
                        # fallback minimal response
                        return {"id": None, "email": user.email, "role": "user"}
                else:
                    # Log detailed response and fall back to DB insert
                    import logging
                    logging.getLogger("uvicorn.error").warning("Supabase insert failed: %s %s", resp.status_code, resp.text)
            except Exception as e:
                import logging
                logging.getLogger("uvicorn.error").exception("Supabase insert attempt failed: %s", e)

        # Fallback: direct DB insert
        pool = await db.get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO accounts (email, password_hash) VALUES ($1,$2) RETURNING id, email, role",
                user.email,
                hashed,
            )
            return {"id": str(row["id"]), "email": row["email"], "role": row["role"]}

    except Exception as e:
        # Log full exception for debugging but avoid leaking sensitive details to client
        try:
            import logging
            logging.getLogger("uvicorn.error").exception("Register failed: %s", e)
        except Exception:
            pass
        # Return a generic error to client with minimal detail
        raise HTTPException(status_code=500, detail="Internal server error while creating account. Check server logs for details.")


@app.post("/auth/sso", response_model=models.UserOut)
async def sso_login(body: models.SSOIn):
    """Create or return an account for social/OAuth sign-ins.

    This accepts an email (from the frontend after successful Google sign-in)
    and ensures an `accounts` row exists. For social accounts we generate a
    random password hash to satisfy the NOT NULL constraint.
    """
    import logging
    try:
        email = (body.email or "").strip().lower()
        if not email:
            raise HTTPException(status_code=400, detail="Missing email")

        # Try Supabase client first (will use service_role if configured)
        try:
            client = supabase_client.get_client()
        except Exception:
            client = None

        if client:
            try:
                existing = client.table("accounts").select("id,email,role").eq("email", email).execute()
                data = existing.data or []
                if data:
                    row = data[0]
                    return {"id": str(row.get("id")), "email": row.get("email"), "role": row.get("role")}

                # create with a random hashed password
                rand_pwd = secrets.token_urlsafe(32)
                pwd_hash = auth.get_password_hash(rand_pwd)
                payload = {"email": email, "password_hash": pwd_hash, "role": "user"}
                res = client.table("accounts").insert(payload).execute()
                if res.data and isinstance(res.data, list):
                    r = res.data[0]
                    return {"id": str(r.get("id")), "email": r.get("email"), "role": r.get("role")}
            except Exception:
                logging.getLogger("uvicorn.error").exception("Supabase SSO upsert failed")

        # Fallback to DB pool
        pool = await db.get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT id, email, role FROM accounts WHERE email=$1", email)
            if row:
                return {"id": str(row["id"]), "email": row["email"], "role": row["role"]}

            rand_pwd = secrets.token_urlsafe(32)
            pwd_hash = auth.get_password_hash(rand_pwd)
            row = await conn.fetchrow(
                "INSERT INTO accounts (email, password_hash) VALUES ($1,$2) RETURNING id, email, role",
                email,
                pwd_hash,
            )
            return {"id": str(row["id"]), "email": row["email"], "role": row["role"]}

    except HTTPException:
        raise
    except Exception as e:
        try:
            logging.getLogger("uvicorn.error").exception("SSO register failed: %s", e)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="Internal server error while processing SSO login")


@app.post("/call/start")
async def call_start(
    workflow_id: str | None = None,
    customer_phone: str | None = None,
    body: CallStartIn | None = Body(None),
    user=Depends(get_current_user),
):
    if body is not None:
        workflow_id = body.workflow_id
        customer_phone = body.customer_phone
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("INSERT INTO calls (workflow_id, customer_phone, status, start_time) VALUES ($1,$2,$3,NOW()) RETURNING id,status,created_at", workflow_id, customer_phone, 'in_progress')
        try:
            calls_started.inc()
        except Exception:
            pass
        return {"call_id": str(row["id"]), "status": row["status"], "started_at": row["created_at"]}


@app.post("/call/reply")
async def call_reply(
    call_id: str | None = None,
    speaker: str | None = None,
    text: str | None = None,
    body: CallReplyIn | None = Body(None),
):
    if body is not None:
        call_id, speaker, text = body.call_id, body.speaker, body.text
    if USE_SUPABASE_SDK:
        supabase_client.rest_insert_conversation_log(call_id, speaker, text)
        try:
            conversation_logs_inserted.inc()
        except Exception:
            pass
        return {"ok": True, "via": "supabase_sdk"}
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO conversation_logs (call_id, speaker, text, created_at) VALUES ($1,$2,$3,NOW())", call_id, speaker, text)
    try:
        conversation_logs_inserted.inc()
    except Exception:
        pass
    return {"ok": True, "via": "db_pool"}


@app.post("/nlu/parse", response_model=NLUParseOut)
async def nlu_parse(body: NLUParseIn):
    nlu = get_nlu()
    parsed = nlu.parse_text(body.text)
    # Persist intent/entities into DB for management
    try:
        pool = await db.get_pool()
        async with pool.acquire() as conn:
            # Insert intent summary into call_intents if a call_id is provided
            call_id = getattr(body, 'call_id', None)
            intent = parsed.get("intent") or {}
            intent_name = intent.get("name") or intent.get("intent")
            confidence = intent.get("confidence") or intent.get("score") or 0.0
            if call_id and intent_name:
                await conn.execute(
                    "INSERT INTO call_intents(call_id, intent_name, count, accuracy) VALUES($1,$2,$3,$4)",
                    call_id, intent_name, 1, float(confidence)
                )
            # Insert entities into call_entities if provided
            entities = parsed.get("entities") or []
            if call_id and isinstance(entities, list):
                for ent in entities:
                    name = ent.get("name") or ent.get("entity")
                    value = ent.get("value") or ent.get("text")
                    if name and value is not None:
                        await conn.execute(
                            "INSERT INTO call_entities(call_id, entity_name, value) VALUES($1,$2,$3)",
                            call_id, name, str(value)
                        )
    except Exception as e:
        # Non-fatal: return parse result even if DB logging fails
        import logging
        logging.getLogger("uvicorn").warning(f"Failed to persist NLU parse: {e}")
    return parsed


@app.post("/stt/transcribe")
async def stt_transcribe(file: UploadFile = File(...)):
    """Accept an audio file upload and return transcription using local STT adapter."""
    try:
        data = await file.read()
        from .stt import transcribe_bytes

        try:
            stt_requests.inc()
        except Exception:
            pass
        res = transcribe_bytes(data)
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    payload = generate_latest(get_registry())
    return Response(payload, media_type=CONTENT_TYPE_LATEST)


@app.get("/reports/{workflow_id}")
async def get_report(workflow_id: str, user=Depends(get_current_user)):
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM v_workflow_summary WHERE workflow_id=$1", workflow_id)
    if not row:
        raise HTTPException(status_code=404, detail="Report not found")
    # Convert asyncpg record to dict
    return dict(row)


@app.post("/conversation/next", response_model=ConversationOut)
async def conversation_next(body: ConversationIn):
    result = process_turn(body.call_id, body.speaker, body.text)
    return result


@app.post("/call/originate")
async def call_originate(channel: str, exten: str, context: str = "default", callerid: str | None = None):
    res = originate(channel=channel, exten=exten, context=context, callerid=callerid)
    return res


@app.post("/conversation/agent")
async def conversation_agent(
    text: str | None = None,
    call_id: str | None = None,
    body: ConversationAgentIn | None = Body(None),
):
    if body is not None:
        text = body.text
        call_id = body.call_id
    agent = get_agent()
    ctx = {"call_id": call_id} if call_id else {}
    res = await agent.respond(text, ctx)
    return res


@app.get("/intents")
async def list_intents():
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, description, category FROM intents ORDER BY name ASC"
        )
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "description": r["description"],
            "category": r["category"],
        }
        for r in rows
    ]


# --------------- Helper HTML for popup callback ---------------

def _callback_html_success(provider: str, token: str, web_nonce: str | None = None) -> str:
        from string import Template
        tmpl = Template("""<!doctype html>
<html><head><meta charset='utf-8'><title>Login Success</title></head>
<body>
<script>
    try {
        if (window.opener) {
            window.opener.postMessage({"type":"oauth","provider":"$provider","ok":true,"token":"$token","web_nonce":"$web_nonce"}, "*");
        }
    } catch (e) {}
    window.close();
    document.body.innerText = 'You can close this window.';
</script>
</body></html>""")
        return tmpl.substitute(provider=provider, token=token, web_nonce=web_nonce or "")


def _callback_html_error(provider: str, message: str) -> str:
        from string import Template
        from html import escape
        msg = escape(message or "unknown error")
        tmpl = Template("""<!doctype html>
<html><head><meta charset='utf-8'><title>Login Error</title></head>
<body>
<script>
    try {
        if (window.opener) {
            window.opener.postMessage({"type":"oauth","provider":"$provider","ok":false,"error":"$msg"}, "*");
        }
    } catch (e) {}
    document.body.innerText = 'OAuth failed: $msg';
</script>
</body></html>""")
        return tmpl.substitute(provider=provider, msg=msg)


@app.get("/entities")
async def list_entities():
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, description, type FROM entities ORDER BY name ASC"
        )
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "description": r["description"],
            "type": r["type"],
        }
        for r in rows
    ]


@app.get("/calls/{call_id}/logs")
async def get_call_logs(call_id: str, limit: int | None = None):
    """Return recent conversation logs for a call."""
    if USE_SUPABASE_SDK:
        data = supabase_client.rest_get_call_logs(call_id)
        if limit is not None and isinstance(data, list):
            data = data[-limit:]
        return data
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        if limit is not None:
            rows = await conn.fetch(
                "SELECT id, speaker, text, intent, confidence, created_at FROM conversation_logs WHERE call_id=$1 ORDER BY created_at ASC LIMIT $2",
                call_id,
                limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT id, speaker, text, intent, confidence, created_at FROM conversation_logs WHERE call_id=$1 ORDER BY created_at ASC",
                call_id,
            )
    return [
        {
            "id": str(r["id"]),
            "speaker": r["speaker"],
            "text": r["text"],
            "intent": r["intent"],
            "confidence": r["confidence"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]


@app.websocket("/ws/calls/{call_id}/logs")
async def ws_call_logs(websocket: WebSocket, call_id: str):
    """Realtime call logs: if Supabase SDK enabled use realtime channel;
    otherwise fall back to polling.
    """
    await websocket.accept()
    if USE_SUPABASE_SDK:
        try:
            sub = supabase_client.subscribe_call_logs(call_id)
        except Exception as e:
            await websocket.send_text(json.dumps({"event": "error", "message": str(e)}))
            await websocket.close()
            return
        try:
            # Send initial snapshot via REST for immediate UI display
            try:
                snapshot = supabase_client.rest_get_call_logs(call_id)
                await websocket.send_text(json.dumps({"event": "snapshot", "data": snapshot}))
            except Exception:
                pass
            while True:
                payload = await sub.queue.get()
                # Supabase realtime payload contains commit record under 'new'
                new_row = payload.get('new') or {}
                await websocket.send_text(json.dumps({"event": "new_log", "data": new_row}))
        except WebSocketDisconnect:
            sub.close()
            return
    else:
        last_count = 0
        try:
            # Initial snapshot
            pool = await db.get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, speaker, text, intent, confidence, created_at FROM conversation_logs WHERE call_id=$1 ORDER BY created_at ASC",
                    call_id,
                )
                initial = [
                    {
                        "id": str(r["id"]),
                        "speaker": r["speaker"],
                        "text": r["text"],
                        "intent": r["intent"],
                        "confidence": r["confidence"],
                        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                    }
                    for r in rows
                ]
                await websocket.send_text(json.dumps({"event": "snapshot", "data": initial}))
                last_count = len(rows)
            while True:
                pool = await db.get_pool()
                async with pool.acquire() as conn:
                    rows = await conn.fetch(
                        "SELECT id, speaker, text, intent, confidence, created_at FROM conversation_logs WHERE call_id=$1 ORDER BY created_at ASC",
                        call_id,
                    )
                if len(rows) > last_count:
                    new_rows = rows[last_count:]
                    payload = [
                        {
                            "id": str(r["id"]),
                            "speaker": r["speaker"],
                            "text": r["text"],
                            "intent": r["intent"],
                            "confidence": r["confidence"],
                            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                        }
                        for r in new_rows
                    ]
                    await websocket.send_text(json.dumps({"event": "new_logs", "data": payload}))
                    last_count = len(rows)
                await asyncio.sleep(2)
        except WebSocketDisconnect:
            return


@app.websocket("/ws/call/audio")
async def ws_voice_stream(websocket: WebSocket, call_id: str = None):
    """Voice streaming endpoint for realtime audio communication.
    
    Handles bidirectional audio:
    - Client sends audio chunks (microphone)
    - Server responds with transcription, LLM response, and synthesized audio
    """
    await voice_stream.voice_stream_handler(websocket, call_id)
