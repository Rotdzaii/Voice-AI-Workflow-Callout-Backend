from __future__ import annotations
import secrets
from typing import Optional, Tuple, Dict, Any

import httpx
from jose import jwt, JWTError

from .config import env_str
from . import auth


ALGORITHM = "HS256"
SECRET_FOR_STATE = env_str("SUPABASE_JWT_SECRET") or env_str("JWT_SECRET") or "change-me-in-prod"


def build_state(provider: str) -> str:
    payload = {"p": provider, "nonce": secrets.token_urlsafe(8)}
    return jwt.encode(payload, SECRET_FOR_STATE, algorithm=ALGORITHM)


def verify_state(state: Optional[str], expected_provider: str) -> bool:
    if not state:
        return False
    try:
        data = jwt.decode(state, SECRET_FOR_STATE, algorithms=[ALGORITHM])
        return data.get("p") == expected_provider
    except JWTError:
        return False


# -------------------- Google OAuth --------------------

def google_auth_url() -> str:
    client_id = env_str("GOOGLE_CLIENT_ID")
    redirect_uri = env_str("GOOGLE_REDIRECT_URI")
    if not client_id or not redirect_uri:
        raise RuntimeError("Missing GOOGLE_CLIENT_ID/GOOGLE_REDIRECT_URI")
    scope = "openid email profile"
    state = build_state("google")
    from urllib.parse import urlencode
    params = urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
        "access_type": "online",
        "prompt": "consent",
        "state": state,
    })
    return f"https://accounts.google.com/o/oauth2/v2/auth?{params}"


async def google_exchange_code(code: str) -> Dict[str, Any]:
    client_id = env_str("GOOGLE_CLIENT_ID")
    client_secret = env_str("GOOGLE_CLIENT_SECRET")
    redirect_uri = env_str("GOOGLE_REDIRECT_URI")
    if not client_id or not client_secret or not redirect_uri:
        raise RuntimeError("Missing Google OAuth envs")
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()


async def google_userinfo(access_token: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()


# -------------------- GitHub OAuth --------------------

def github_auth_url() -> str:
    client_id = env_str("GITHUB_CLIENT_ID")
    redirect_uri = env_str("GITHUB_REDIRECT_URI")
    if not client_id or not redirect_uri:
        raise RuntimeError("Missing GITHUB_CLIENT_ID/GITHUB_REDIRECT_URI")
    state = build_state("github")
    from urllib.parse import urlencode
    params = urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "read:user user:email",
        "state": state,
        "allow_signup": "true",
    })
    return f"https://github.com/login/oauth/authorize?{params}"


async def github_exchange_code(code: str) -> Dict[str, Any]:
    client_id = env_str("GITHUB_CLIENT_ID")
    client_secret = env_str("GITHUB_CLIENT_SECRET")
    redirect_uri = env_str("GITHUB_REDIRECT_URI")
    if not client_id or not client_secret or not redirect_uri:
        raise RuntimeError("Missing GitHub OAuth envs")
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()


async def github_userinfo(access_token: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as client:
        user_resp = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/vnd.github+json"},
        )
        user_resp.raise_for_status()
        user = user_resp.json()
        email = user.get("email")
        if not email:
            emails_resp = await client.get(
                "https://api.github.com/user/emails",
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/vnd.github+json"},
            )
            emails_resp.raise_for_status()
            emails = emails_resp.json() or []
            primary = next((e for e in emails if e.get("primary") and e.get("verified")), None)
            email = (primary or (emails[0] if emails else {})).get("email")
        return {"id": user.get("id"), "login": user.get("login"), "name": user.get("name"), "email": email}


# -------------------- Account helper --------------------

async def get_or_create_account_by_email(conn, email: str) -> Tuple[str, Optional[str]]:
    # Returns (user_id, role)
    row = await conn.fetchrow("SELECT id, email, role FROM accounts WHERE email=$1", email)
    if row:
        return str(row["id"]), row["role"]
    # Create a new account with a random password hash to satisfy NOT NULL constraints
    random_pw = secrets.token_urlsafe(16)
    hashed = auth.get_password_hash(random_pw)
    row = await conn.fetchrow(
        "INSERT INTO accounts (email, password_hash) VALUES ($1,$2) RETURNING id, role",
        email,
        hashed,
    )
    return str(row["id"]), row["role"]
