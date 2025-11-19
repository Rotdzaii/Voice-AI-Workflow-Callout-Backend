import os
import time
import uuid
import asyncio
import socket
from urllib.parse import urlparse
import json
from fastapi.testclient import TestClient
from backend.main import app
import backend.main as main
from backend import db
from backend.auth import get_password_hash

client = TestClient(app)

def _db_reachable() -> bool:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        return False
    try:
        u = urlparse(dsn)
        host = u.hostname
        port = u.port or 5432
        if not host:
            return False
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except Exception:
        return False

async def _seed_user(email: str, password: str):
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT id FROM accounts WHERE email=$1", email)
        if existing:
            return existing["id"]
        hashed = get_password_hash(password)
        row = await conn.fetchrow("INSERT INTO accounts (email, password_hash) VALUES ($1,$2) RETURNING id", email, hashed)
        return row["id"]

async def _seed_workflow(user_id: str):
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO workflows (user_id, name, description, workflow_json) VALUES ($1,$2,$3,$4::jsonb) RETURNING id",
            user_id,
            "WS Test WF",
            "Desc",
            json.dumps({"nodes": []}),
        )
        return row["id"]


def test_websocket_logs_flow():
    # Force SDK-off for local tests to avoid external Supabase dependency
    os.environ["SUPABASE_USE_SDK"] = "false"
    main.USE_SUPABASE_SDK = False
    if not _db_reachable():
        return
    email = f"ws_{uuid.uuid4().hex[:6]}@ex.com"
    password = "Secret123!"
    user_id = asyncio.run(_seed_user(email, password))
    wf_id = asyncio.run(_seed_workflow(user_id))

    with TestClient(app) as client:
        # Start a call via API (login to get token)
        r_login = client.post("/auth/token", data={"username": email, "password": password})
        assert r_login.status_code == 200
        token = r_login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        r_start = client.post("/call/start", params={"workflow_id": wf_id, "customer_phone": "+84111111111"}, headers=headers)
        assert r_start.status_code == 200
        call_id = r_start.json()["call_id"]

        # Ensure there is at least one log before opening WS (to test snapshot)
        r_reply = client.post("/call/reply", params={"call_id": call_id, "speaker": "agent", "text": "Hello WS"})
        assert r_reply.status_code == 200

        with client.websocket_connect(f"/ws/calls/{call_id}/logs") as ws:
            # Expect a snapshot event immediately
            snapshot = ws.receive_json()
            assert snapshot.get("event") == "snapshot"
            assert isinstance(snapshot.get("data"), list)
            # Send another reply and expect a new_logs/new_log event shortly
            r_reply2 = client.post("/call/reply", params={"call_id": call_id, "speaker": "user", "text": "Xin chao"})
            assert r_reply2.status_code == 200
            # Small wait to avoid concurrent operation on same pool connection
            time.sleep(0.2)
            msg = ws.receive_json()
            assert msg.get("event") in {"new_logs", "new_log"}
