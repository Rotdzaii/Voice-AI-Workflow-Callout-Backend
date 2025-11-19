from fastapi import FastAPI, Depends, HTTPException, status, WebSocket, WebSocketDisconnect, Body
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
from .deeppavlov_client import get_agent

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    try:
        await db.get_pool()
    except Exception:
        # Keep app running even if DB is not reachable at boot
        pass
    yield
    # Shutdown
    try:
        await db.close_pool()
    except Exception:
        pass


app = FastAPI(title="VoiceAI - Backend (MVP)", lifespan=lifespan)

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
    pool = await db.get_pool()
    hashed = auth.get_password_hash(user.password)
    async with pool.acquire() as conn:
        row = await conn.fetchrow("INSERT INTO accounts (email, password_hash) VALUES ($1,$2) RETURNING id, email, role", user.email, hashed)
        return {"id": str(row["id"]), "email": row["email"], "role": row["role"]}


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
        return {"ok": True, "via": "supabase_sdk"}
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO conversation_logs (call_id, speaker, text, created_at) VALUES ($1,$2,$3,NOW())", call_id, speaker, text)
    return {"ok": True, "via": "db_pool"}


@app.post("/nlu/parse", response_model=NLUParseOut)
async def nlu_parse(body: NLUParseIn):
    nlu = get_nlu()
    parsed = nlu.parse_text(body.text)
    return parsed


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
