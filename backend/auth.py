from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

from passlib.context import CryptContext
from jose import JWTError, jwt
from .config import SUPABASE_JWT_SECRET, JWT_ISSUER, JWT_AUDIENCE, ACCESS_TOKEN_TTL_SECONDS, JWT_KEY_ID

# Load secret from centralized config. Prefer Supabase JWT secret if provided.
SECRET_KEY = SUPABASE_JWT_SECRET or "change-me-in-prod"
ALGORITHM = "HS256"
DEFAULT_EXPIRE_SECONDS = ACCESS_TOKEN_TTL_SECONDS or 3600

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(claims: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    now = datetime.now(timezone.utc)
    exp = now + (expires_delta if expires_delta else timedelta(seconds=DEFAULT_EXPIRE_SECONDS))
    base = {
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "kid": JWT_KEY_ID,
    }
    # Do not allow overriding security-critical fields
    for k in ["iss", "aud", "iat", "exp", "kid"]:
        if k in claims:
            claims.pop(k)
    payload = {**base, **claims}
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    return token


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
        )
        return payload
    except JWTError:
        return None
