"""Supabase REST & Realtime helpers.

Uses service role key if available for server-side inserts; falls back to anon key.
Realtime subscriptions deliver changes via an asyncio.Queue for easy fan-out.
"""
from __future__ import annotations
import asyncio
from typing import Optional, Tuple, Any, Dict

try:
    from supabase import create_client, Client as SupabaseClient
except Exception:  # pragma: no cover
    create_client = None  # type: ignore
    Client = object  # type: ignore

from .config import SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY

_client: Optional[SupabaseClient] = None


def get_client() -> Optional[SupabaseClient]:
    global _client
    if _client is not None:
        return _client
    if not create_client or not SUPABASE_URL:
        return None
    key = SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY
    if not key:
        return None
    _client = create_client(SUPABASE_URL, key)
    return _client


# ---------- REST helpers ----------

def rest_get_call_logs(call_id: str):
    client = get_client()
    if not client:
        raise RuntimeError("Supabase client not configured")
    res = client.table("conversation_logs").select(
        "id,speaker,text,intent,confidence,created_at"
    ).eq("call_id", call_id).order("created_at", desc=False).execute()
    return res.data or []


def rest_insert_conversation_log(call_id: str, speaker: str, text: str, intent: Optional[str] = None, confidence: Optional[float] = None):
    client = get_client()
    if not client:
        raise RuntimeError("Supabase client not configured")
    payload: Dict[str, Any] = {
        "call_id": call_id,
        "speaker": speaker,
        "text": text,
    }
    if intent is not None:
        payload["intent"] = intent
    if confidence is not None:
        payload["confidence"] = confidence
    res = client.table("conversation_logs").insert(payload).execute()
    return res.data


def rest_update_call_recording(call_id: str, recording_url: str):
    client = get_client()
    if not client:
        raise RuntimeError("Supabase client not configured")
    payload = {"recording_url": recording_url}
    res = client.table("calls").update(payload).eq("id", call_id).execute()
    return res.data


# ---------- Realtime helpers ----------

class RealtimeSubscription:
    def __init__(self, channel, queue: "asyncio.Queue[dict]"):
        self.channel = channel
        self.queue = queue

    def close(self):
        try:
            # Unsubscribe or remove channel depending on lib version
            if hasattr(self.channel, "unsubscribe"):
                self.channel.unsubscribe()
        except Exception:
            pass


def subscribe_call_logs(call_id: str) -> RealtimeSubscription:
    client = get_client()
    if not client:
        raise RuntimeError("Supabase client not configured")
    channel = client.channel(f"realtime_call_{call_id}")
    q: "asyncio.Queue[dict]" = asyncio.Queue()

    def _handler(payload):  # payload is a dict
        try:
            q.put_nowait(payload)
        except Exception:
            pass

    channel.on(
        "postgres_changes",
        {
            "event": "INSERT",
            "schema": "public",
            "table": "conversation_logs",
            "filter": f"call_id=eq.{call_id}",
        },
        _handler,
    )
    channel.subscribe()
    return RealtimeSubscription(channel, q)
