"""Centralized app configuration.
Loads .env on import and exposes typed accessors for common settings.
"""
from __future__ import annotations
import os
from typing import Optional
from dotenv import load_dotenv, dotenv_values

# Load .env from project root when this module is imported
loaded = load_dotenv()


def env_str(key: str, default: Optional[str] = None) -> Optional[str]:
    return os.getenv(key, default)


def env_int(key: str, default: Optional[int] = None) -> Optional[int]:
    try:
        v = os.getenv(key)
        return int(v) if v is not None else default
    except ValueError:
        return default


# Frequently used settings
DATABASE_URL = env_str("DATABASE_URL")
SUPABASE_JWT_SECRET = env_str("SUPABASE_JWT_SECRET") or env_str("JWT_SECRET")
APP_PORT = env_int("APP_PORT", 8000)
APP_ENV = env_str("APP_ENV", "development")

# Supabase REST/Realtime
SUPABASE_URL = env_str("SUPABASE_URL")
SUPABASE_ANON_KEY = env_str("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_ROLE_KEY = env_str("SUPABASE_SERVICE_ROLE_KEY")
USE_SUPABASE_SDK = env_str("SUPABASE_USE_SDK", "false").lower() in {"1", "true", "yes"}

# NLU & Conversation
NLU_ENGINE = env_str("NLU_ENGINE", "simple")  # simple | phobert
DEEPPAVLOV_URL = env_str("DEEPPAVLOV_URL")

# Dev helpers
DEV_AUTH_ALLOW_NO_DB = env_str("DEV_AUTH_ALLOW_NO_DB", "false").lower() in {"1", "true", "yes"}

# OAuth security settings
SECRET_KEY = env_str("SECRET_KEY") or os.getenv("JWT_SECRET") or "dev-secret-change-me"
STATE_COOKIE_NAME = "oauth_state"
NONCE_COOKIE_NAME = "oauth_nonce"
COOKIE_MAX_AGE_SECONDS = int(env_int("OAUTH_COOKIE_MAX_AGE", 600) or 600)
