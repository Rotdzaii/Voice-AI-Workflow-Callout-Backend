import os
import uuid
import socket
from urllib.parse import urlparse
from fastapi.testclient import TestClient
from backend.main import app
import backend.main as main
from backend import db
from backend.auth import get_password_hash
import asyncio

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


def test_nlu_parse_simple():
    r = client.post("/nlu/parse", json={"text": "tôi đồng ý"})
    assert r.status_code == 200
    data = r.json()
    assert data["intent"]["name"] in {"affirm", "unknown", "chitchat"}


def test_workflow_crud_cycle():
    # Force SDK-off for local tests to avoid external Supabase dependency
    os.environ["SUPABASE_USE_SDK"] = "false"
    main.USE_SUPABASE_SDK = False
    if not _db_reachable():
        return
    email = f"crud_{uuid.uuid4().hex[:6]}@ex.com"
    password = "Secret123!"
    user_id = asyncio.run(_seed_user(email, password))

    # login
    r_login = client.post("/auth/token", data={"username": email, "password": password})
    assert r_login.status_code == 200
    token = r_login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # create workflow
    body = {"name": "WF1", "description": "d1", "workflow_json": {"nodes": []}}
    r_create = client.post("/workflows", json=body, headers=headers)
    assert r_create.status_code == 200
    wf = r_create.json()

    # get
    r_get = client.get(f"/workflows/{wf['id']}", headers=headers)
    assert r_get.status_code == 200

    # update
    r_put = client.put(f"/workflows/{wf['id']}", json={"name": "WF2"}, headers=headers)
    assert r_put.status_code == 200

    # delete
    r_del = client.delete(f"/workflows/{wf['id']}", headers=headers)
    assert r_del.status_code == 200
