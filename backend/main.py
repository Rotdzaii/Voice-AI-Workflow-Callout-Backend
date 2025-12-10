"""FastAPI entrypoint for the VoiceAI backend."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from html import escape
from string import Template
from typing import Any

from fastapi import (
    APIRouter,
    Body,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from importlib import import_module

from . import auth, db, models, oauth, rag_api, supabase_client, voice_stream
from .asterisk import originate
from .config import USE_SUPABASE_SDK
from .conversation import process_turn
from .dependencies import get_current_user
from .deeppavlov_client import get_agent
from .logging_setup import calls_started, conversation_logs_inserted, get_registry, stt_requests
from .routers import workflows as workflows_router
from .streaming import stream_events, StreamMetadata
from .models import (
    CallReplyIn,
    CallStartIn,
    ConversationAgentIn,
    ConversationIn,
    ConversationOut,
    NLUParseIn,
    NLUParseOut,
)
from .nlu import get_nlu

logger = logging.getLogger("uvicorn.error")

@asynccontextmanager
async def lifespan(_: FastAPI):
    """Application lifespan: warm up pools and shut them down gracefully."""
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

    try:
        await db.close_pool()
    except Exception:
        logger.warning("Error during DB pool shutdown", exc_info=True)


app = FastAPI(title="VoiceAI - Backend (MVP)", lifespan=lifespan)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(content=None, status_code=status.HTTP_204_NO_CONTENT)

origins = [
    "http://localhost:5173",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
]

print(f"🔒 CORS Configured for: {origins}")

app.add_middleware(
    try:
        with open("frontend/chat_test.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except Exception:
        return HTMLResponse(content="<h1>Chat test not found</h1>", status_code=404)
        topics=[d.metadata.get("topic") for d in docs],
        scores=scores,
        latencies={
            "retriever": latency_retriever,
            "context": latency_context,
        },
    )

    generator = stream_events(
        prompt,
        metadata,
        include_audio=body.include_audio,
        voice=body.voice,
        rate=body.rate,
    )
    return StreamingResponse(generator, media_type="text/event-stream")


app.include_router(stream_router)

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/chat", response_class=HTMLResponse)
async def chat_test() -> HTMLResponse:
    try:
        with open("frontend/chat_test.html", "r", encoding="utf-8") as fp:
            return HTMLResponse(content=fp.read())
    except Exception:
<<<<<<< HEAD
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
=======
        return HTMLResponse("<h1>Chat test not found</h1>", status_code=404)
>>>>>>> origin/develop


@app.post("/auth/token", response_model=models.Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, email, password_hash, role FROM accounts WHERE email=$1",
            form_data.username,
        )
        if not row or not auth.verify_password(form_data.password, row["password_hash"]):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
        token = auth.create_access_token({"sub": str(row["id"]), "role": row["role"]})
        return {"access_token": token, "token_type": "bearer"}


@app.get("/auth/verify")
@app.get("/auth/me")
async def verify_token(current_user: dict = Depends(get_current_user)):
    """Return the authenticated user's public profile if the bearer token is valid."""
    return current_user


@app.get("/auth/oauth/google/start")
async def oauth_google_start(web_nonce: str | None = None):
    try:
        url, state, nonce, code_verifier = oauth.google_auth_url(web_nonce=web_nonce)
        resp = RedirectResponse(url)
        oauth.set_state_cookie(resp, state)
        oauth.set_nonce_cookie(resp, nonce)
        oauth.set_code_verifier_cookie(resp, code_verifier)
        return resp
    except Exception as exc:
        logger.exception("Google OAuth start failed")
        return JSONResponse({"error": str(exc)}, status_code=503)


@app.get("/auth/oauth/google/callback")
async def oauth_google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    nonce: str | None = None,
):
    state_data = oauth.verify_state(state, "google")
    if not code or not state_data:
        return HTMLResponse(_callback_html_error("google", "missing code/state"), status_code=400)
    web_nonce = state_data.get("w")

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
        app_token = auth.create_access_token(
            {
                "sub": uid,
                "email": email,
                "role": role or "user",
                "provider": "google",
                "name": profile.get("name"),
                "picture": profile.get("picture"),
            }
        )
        return HTMLResponse(_callback_html_success("google", app_token, web_nonce))
    except Exception as exc:
        logger.exception("Google OAuth callback failed")
        return HTMLResponse(_callback_html_error("google", str(exc)), status_code=500)


@app.get("/auth/oauth/github/start")
async def oauth_github_start(web_nonce: str | None = None):
    try:
        url, state = oauth.github_auth_url(web_nonce=web_nonce)
        resp = RedirectResponse(url)
        oauth.set_state_cookie(resp, state)
        return resp
    except Exception as exc:
        logger.exception("GitHub OAuth start failed")
        return JSONResponse({"error": str(exc)}, status_code=503)


@app.get("/auth/oauth/github/callback")
async def oauth_github_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
):
    state_data = oauth.verify_state(state, "github")
    if not code or not state_data:
        return HTMLResponse(_callback_html_error("github", "missing code/state"), status_code=400)
    web_nonce = state_data.get("w")

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
        app_token = auth.create_access_token(
            {
                "sub": uid,
                "email": email,
                "role": role or "user",
                "provider": "github",
                "name": profile.get("name") or profile.get("login"),
                "picture": profile.get("avatar_url"),
            }
        )
        return HTMLResponse(_callback_html_success("github", app_token, web_nonce))
    except Exception as exc:
        logger.exception("GitHub OAuth callback failed")
        return HTMLResponse(_callback_html_error("github", str(exc)), status_code=500)


@app.post("/auth/register", response_model=models.UserOut)
async def register(user: models.UserCreate):
    try:
        hashed = auth.get_password_hash(user.password)
        supabase_url = os.environ.get("SUPABASE_URL")
        supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if supabase_url and supabase_key:
            try:
                import requests

                headers = {
                    "apikey": supabase_key,
                    "Authorization": f"Bearer {supabase_key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=representation",
                }
                payload = {"email": user.email, "password_hash": hashed, "role": "user"}
                resp = requests.post(
                    f"{supabase_url}/rest/v1/accounts",
                    json=payload,
                    headers=headers,
                    timeout=10,
                )
                if resp.status_code in (200, 201):
                    data = resp.json()
                    if isinstance(data, list) and data:
                        row = data[0]
                        return {"id": str(row.get("id")), "email": row.get("email"), "role": row.get("role")}
                    return {"id": None, "email": user.email, "role": "user"}
                logger.warning("Supabase insert failed: %s %s", resp.status_code, resp.text)
            except Exception:
                logger.exception("Supabase insert attempt failed")
        pool = await db.get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO accounts (email, password_hash) VALUES ($1,$2) RETURNING id, email, role",
                user.email,
                hashed,
            )
            return {"id": str(row["id"]), "email": row["email"], "role": row["role"]}
    except Exception as exc:
        logger.exception("Register failed")
        raise HTTPException(status_code=500, detail="Internal server error while creating account. Check server logs for details.") from exc


@app.post("/auth/sso", response_model=models.UserOut)
async def sso_login(body: models.SSOIn):
    try:
        email = (body.email or "").strip().lower()
        if not email:
            raise HTTPException(status_code=400, detail="Missing email")
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
                rand_pwd = secrets.token_urlsafe(32)
                pwd_hash = auth.get_password_hash(rand_pwd)
                payload = {"email": email, "password_hash": pwd_hash, "role": "user"}
                res = client.table("accounts").insert(payload).execute()
                if res.data and isinstance(res.data, list):
                    r = res.data[0]
                    return {"id": str(r.get("id")), "email": r.get("email"), "role": r.get("role")}
            except Exception:
                logger.exception("Supabase SSO upsert failed")
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
    except Exception as exc:
        logger.exception("SSO register failed")
        raise HTTPException(status_code=500, detail="Internal server error while processing SSO login") from exc


@app.post("/call/start")
async def call_start(
    workflow_id: str | None = None,
    customer_phone: str | None = None,
    body: CallStartIn | None = Body(None),
    user: dict[str, Any] | None = Depends(get_current_user),
):
    del user
    if body is not None:
        workflow_id = body.workflow_id
        customer_phone = body.customer_phone
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO calls (workflow_id, customer_phone, status, start_time) VALUES ($1,$2,$3,NOW()) RETURNING id,status,created_at",
            workflow_id,
            customer_phone,
            "in_progress",
        )
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
        await conn.execute(
            "INSERT INTO conversation_logs (call_id, speaker, text, created_at) VALUES ($1,$2,$3,NOW())",
            call_id,
            speaker,
            text,
        )
    try:
        conversation_logs_inserted.inc()
    except Exception:
        pass
    return {"ok": True, "via": "db_pool"}


@app.post("/nlu/parse", response_model=NLUParseOut)
async def nlu_parse(body: NLUParseIn):
    nlu = get_nlu()
    parsed = nlu.parse_text(body.text)
    try:
        pool = await db.get_pool()
        async with pool.acquire() as conn:
            call_id = getattr(body, "call_id", None)
            intent = parsed.get("intent") or {}
            intent_name = intent.get("name") or intent.get("intent")
            confidence = intent.get("confidence") or intent.get("score") or 0.0
            if call_id and intent_name:
                await conn.execute(
                    "INSERT INTO call_intents(call_id, intent_name, count, accuracy) VALUES($1,$2,$3,$4)",
                    call_id,
                    intent_name,
                    1,
                    float(confidence),
                )
            entities = parsed.get("entities") or []
            if call_id and isinstance(entities, list):
                for ent in entities:
                    name = ent.get("name") or ent.get("entity")
                    value = ent.get("value") or ent.get("text")
                    if name and value is not None:
                        await conn.execute(
                            "INSERT INTO call_entities(call_id, entity_name, value) VALUES($1,$2,$3)",
                            call_id,
                            name,
                            str(value),
                        )
    except Exception:
        logger.warning("Failed to persist NLU parse", exc_info=True)
    return parsed


@app.post("/stt/transcribe")
async def stt_transcribe(file: UploadFile = File(...)):
    try:
        data = await file.read()
        from .stt import transcribe_bytes

        try:
            stt_requests.inc()
        except Exception:
            pass
        return transcribe_bytes(data)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/metrics")
async def metrics():
    payload = generate_latest(get_registry())
    return Response(payload, media_type=CONTENT_TYPE_LATEST)


@app.get("/reports/{workflow_id}")
async def get_report(workflow_id: str, user: dict[str, Any] = Depends(get_current_user)):
    del user
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM v_workflow_summary WHERE workflow_id=$1", workflow_id)
    if not row:
        raise HTTPException(status_code=404, detail="Report not found")
    return dict(row)


@app.post("/conversation/next", response_model=ConversationOut)
async def conversation_next(body: ConversationIn):
    return process_turn(body.call_id, body.speaker, body.text)


@app.post("/call/originate")
async def call_originate(channel: str, exten: str, context: str = "default", callerid: str | None = None):
    return originate(channel=channel, exten=exten, context=context, callerid=callerid)


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
    return await agent.respond(text, ctx)


@app.get("/intents")
async def list_intents():
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, name, description, category FROM intents ORDER BY name ASC")
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
        rows = await conn.fetch("SELECT id, name, description, type FROM entities ORDER BY name ASC")
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
    await websocket.accept()
    if USE_SUPABASE_SDK:
        try:
            sub = supabase_client.subscribe_call_logs(call_id)
        except Exception as exc:
            await websocket.send_text(json.dumps({"event": "error", "message": str(exc)}))
            await websocket.close()
            return
        try:
            try:
                snapshot = supabase_client.rest_get_call_logs(call_id)
                await websocket.send_text(json.dumps({"event": "snapshot", "data": snapshot}))
            except Exception:
                pass
            while True:
                payload = await sub.queue.get()
                new_row = payload.get("new") or {}
                await websocket.send_text(json.dumps({"event": "new_log", "data": new_row}))
        except WebSocketDisconnect:
            sub.close()
            return
    else:
        last_count = 0
        try:
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


def _callback_html_error(provider: str, message: str) -> str:

