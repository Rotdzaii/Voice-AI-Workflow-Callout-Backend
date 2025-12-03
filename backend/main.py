from fastapi import FastAPI, Depends, HTTPException, status, WebSocket, WebSocketDisconnect, Body, UploadFile, File, Response
from contextlib import asynccontextmanager
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from . import auth, db, models
import asyncio
import json
from .config import USE_SUPABASE_SDK
from . import supabase_client
from .nlu import get_nlu
from .conversation import process_turn
from .asterisk import originate
from .models import NLUParseIn, NLUParseOut, ConversationIn, ConversationOut, CallStartIn, CallReplyIn, ConversationAgentIn
from fastapi.middleware.cors import CORSMiddleware
from .config import env_str
import os
from .logging_setup import configure_logging, get_registry, calls_started, conversation_logs_inserted, stt_requests, tts_requests
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from .deeppavlov_client import get_agent
from . import rag_api
import secrets

# Load environment variables from .env early to ensure consistency
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    try:
        await db.get_pool()
    except Exception:
        # Keep app running even if DB is not reachable at boot
        pass
    # Startup: avoid background RAG init to keep startup clean; rely on lazy init in /rag
    try:
        if os.environ.get("RAG_EAGER_INIT", "0") == "1":
            try:
                rag_api._ensure_rag()
            except Exception as e:
                try:
                    import logging
                    logging.getLogger("uvicorn").exception(f"Eager RAG init failed: {e}")
                except Exception:
                    pass
    except Exception:
        pass
    yield
    # Shutdown
    try:
        await db.close_pool()
    except Exception:
        pass


app = FastAPI(title="VoiceAI - Backend (MVP)", lifespan=lifespan)

# Register RAG router
app.include_router(rag_api.router, prefix="/rag")

# Enable CORS for frontend integration
allowed_origins = env_str("ALLOWED_ORIGINS", "*")
origins = [o.strip() for o in allowed_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/debug/db_ping")
async def db_ping():
    """Attempt a simple DB query to verify connectivity. Returns 200 with ok=false on failure."""
    try:
        pool = await db.get_pool()
        async with pool.acquire() as conn:
            val = await conn.fetchval("SELECT 1")
        return {"ok": True, "result": val}
    except Exception as e:
        # Avoid leaking internals; return minimal info
        return {"ok": False, "error": str(e)}


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


@app.post("/workflows", response_model=models.WorkflowOut)
async def create_workflow(w: models.WorkflowCreate, user=Depends(get_current_user)):
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        wf_json = json.dumps(w.workflow_json) if isinstance(w.workflow_json, (dict, list)) else w.workflow_json
        row = await conn.fetchrow(
            "INSERT INTO workflows (user_id, name, description, workflow_json) VALUES ($1,$2,$3,$4::jsonb) RETURNING id, user_id, name, description, status",
            user["id"], w.name, w.description, wf_json,
        )
        return {"id": str(row["id"]), "user_id": str(row["user_id"]), "name": row["name"], "description": row["description"], "status": row["status"]}


@app.get("/workflows")
async def list_workflows(user=Depends(get_current_user)):
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, user_id, name, description, status FROM workflows WHERE user_id=$1", user["id"])
    return [{"id": str(r["id"]), "user_id": str(r["user_id"]), "name": r["name"], "description": r["description"], "status": r["status"]} for r in rows]
@app.get("/workflows/{workflow_id}", response_model=models.WorkflowOut)
async def get_workflow(workflow_id: str, user=Depends(get_current_user)):
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id, user_id, name, description, status FROM workflows WHERE id=$1", workflow_id)
    if not row:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {"id": str(row["id"]), "user_id": str(row["user_id"]), "name": row["name"], "description": row["description"], "status": row["status"]}


@app.put("/workflows/{workflow_id}", response_model=models.WorkflowOut)
async def update_workflow(workflow_id: str, patch: dict, user=Depends(get_current_user)):
    updates = []
    values = []
    idx = 1
    for field in ["name", "description", "status", "workflow_json"]:
        if field in patch and patch[field] is not None:
            if field == "workflow_json":
                updates.append(f"{field}=${idx}::jsonb")
                jval = json.dumps(patch[field]) if isinstance(patch[field], (dict, list)) else patch[field]
                values.append(jval)
            else:
                updates.append(f"{field}=${idx}")
                values.append(patch[field])
            idx += 1
    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")
    sql = f"UPDATE workflows SET {', '.join(updates)} WHERE id=${idx} RETURNING id, user_id, name, description, status"
    values.append(workflow_id)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *values)
    if not row:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {"id": str(row["id"]), "user_id": str(row["user_id"]), "name": row["name"], "description": row["description"], "status": row["status"]}


@app.delete("/workflows/{workflow_id}")
async def delete_workflow(workflow_id: str, user=Depends(get_current_user)):
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM workflows WHERE id=$1", workflow_id)
    return {"deleted": True, "result": result}


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
