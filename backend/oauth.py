from __future__ import annotations
import secrets
import hmac
import hashlib
from typing import Optional, Tuple, Dict, Any

import httpx
from jose import jwt, JWTError

from .config import env_str, SECRET_KEY, STATE_COOKIE_NAME, NONCE_COOKIE_NAME, COOKIE_MAX_AGE_SECONDS, CODE_VERIFIER_COOKIE_NAME
from . import auth


ALGORITHM = "HS256"
SECRET_FOR_STATE = env_str("SUPABASE_JWT_SECRET") or env_str("JWT_SECRET") or "change-me-in-prod"


def _sign(value: str) -> str:
    sig = hmac.new(SECRET_KEY.encode("utf-8"), msg=value.encode("utf-8"), digestmod=hashlib.sha256).hexdigest()
    return f"{value}|{sig}"


def _verify_signed(cookie_value: Optional[str], expected: Optional[str]) -> bool:
    if not cookie_value or not expected:
        return False
    try:
        val, sig = cookie_value.split("|", 1)
    except ValueError:
        return False
    calc = hmac.new(SECRET_KEY.encode("utf-8"), msg=val.encode("utf-8"), digestmod=hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, calc) and hmac.compare_digest(val, expected)


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


def set_state_cookie(response, state: str):
    response.set_cookie(
        STATE_COOKIE_NAME,
        _sign(state),
        max_age=COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=False,  # set True on HTTPS
    )


def set_nonce_cookie(response, nonce: str):
    response.set_cookie(
        NONCE_COOKIE_NAME,
        _sign(nonce),
        max_age=COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=False,  # set True on HTTPS
    )


def verify_cookies(state_cookie: Optional[str], nonce_cookie: Optional[str], state: Optional[str], nonce: Optional[str]) -> bool:
    ok_state = _verify_signed(state_cookie, state) if (state_cookie and state) else True
    ok_nonce = _verify_signed(nonce_cookie, nonce) if (nonce_cookie and nonce) else True
    return ok_state and ok_nonce


def set_code_verifier_cookie(response, code_verifier: str):
    response.set_cookie(
        CODE_VERIFIER_COOKIE_NAME,
        _sign(code_verifier),
        max_age=COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=False,
    )


def extract_signed_code_verifier(raw_cookie: Optional[str]) -> Optional[str]:
    if not raw_cookie:
        return None
    try:
        val, sig = raw_cookie.split("|", 1)
    except ValueError:
        return None
    calc = hmac.new(SECRET_KEY.encode("utf-8"), msg=val.encode("utf-8"), digestmod=hashlib.sha256).hexdigest()
    if hmac.compare_digest(sig, calc):
        return val
    return None


# -------------------- Google OAuth --------------------

def _code_verifier() -> str:
    # RFC 7636: length 43-128 chars allowed; use 64
    return secrets.token_urlsafe(64)


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    import base64
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def google_auth_url() -> Tuple[str, str, str, str]:
    client_id = env_str("GOOGLE_CLIENT_ID")
    redirect_uri = env_str("GOOGLE_REDIRECT_URI")
    if not client_id or not redirect_uri:
        raise RuntimeError("Missing GOOGLE_CLIENT_ID/GOOGLE_REDIRECT_URI")
    scope = "openid email profile"
    state = build_state("google")
    nonce = secrets.token_urlsafe(12)
    code_verifier = _code_verifier()
    code_challenge = _code_challenge(code_verifier)
    from urllib.parse import urlencode
    params = urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
        "access_type": "online",
        "prompt": "consent",
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    })
    return f"https://accounts.google.com/o/oauth2/v2/auth?{params}", state, nonce, code_verifier


async def google_exchange_code(code: str, code_verifier: Optional[str]) -> Dict[str, Any]:
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
                **({"code_verifier": code_verifier} if code_verifier else {}),
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
        data = resp.json()
        return {
            "id": data.get("sub"),
            "email": data.get("email"),
            "name": data.get("name"),
            "picture": data.get("picture"),
        }


# -------------------- GitHub OAuth --------------------

def github_auth_url() -> Tuple[str, str]:
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
    return f"https://github.com/login/oauth/authorize?{params}", state


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
        return {
            "id": user.get("id"),
            "login": user.get("login"),
            "name": user.get("name"),
            "email": email,
            "avatar_url": user.get("avatar_url"),
        }


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
