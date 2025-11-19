import os
import uuid
import socket
from urllib.parse import urlparse
from fastapi.testclient import TestClient
from backend.main import app
import backend.main as main
from backend.auth import get_password_hash
from backend import db
import asyncio
import json

async def _seed_both(email: str, password: str):
    uid = await _seed_user(email, password)
    wid = await _seed_workflow(uid)
    return uid, wid

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
            "Test WF",
            "Desc",
            json.dumps({"nodes": []}),
        )
        return row["id"]


def test_register_login_and_call_flow():
    # Force SDK-off for local tests to avoid external Supabase dependency
    os.environ["SUPABASE_USE_SDK"] = "false"
    main.USE_SUPABASE_SDK = False
    # Skip if DB unreachable
    if not _db_reachable():
        return
    email = f"tester_{uuid.uuid4().hex[:6]}@ex.com"
    password = "Secret123!"

    # Seed user & workflow in a single event loop run to avoid pool/loop conflicts
    user_id, wf_id = asyncio.run(_seed_both(email, password))

    with TestClient(app) as client:
        # Login
        r_login = client.post("/auth/token", data={"username": email, "password": password})
        assert r_login.status_code == 200, r_login.text
        token = r_login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Start call
        r_start = client.post("/call/start", params={"workflow_id": wf_id, "customer_phone": "+84123456789"}, headers=headers)
        assert r_start.status_code == 200, r_start.text
        call_id = r_start.json()["call_id"]

        # Add reply
        r_reply = client.post("/call/reply", params={"call_id": call_id, "speaker": "agent", "text": "Xin chao"})
        assert r_reply.status_code == 200

        # Fetch logs REST
        r_logs = client.get(f"/calls/{call_id}/logs")
        assert r_logs.status_code == 200
        data = r_logs.json()
        assert len(data) >= 1
        assert data[-1]["text"] == "Xin chao"
