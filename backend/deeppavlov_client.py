"""Client wrapper for Deeppavlov-Agent service.

Expected API (example): POST {DEEPPAVLOV_URL}/respond {json: {text, context}}
Returns {reply, intents, entities}
"""
from __future__ import annotations
import httpx
from .config import DEEPPAVLOV_URL

class DeeppavlovClient:
    def __init__(self, base_url: str | None):
        self.base_url = base_url.rstrip("/") if base_url else None

    async def respond(self, text: str, context: dict | None = None):
        if not self.base_url:
            # Fallback response
            return {"reply": "(agent offline)", "intents": [], "entities": []}
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.post(f"{self.base_url}/respond", json={"text": text, "context": context or {}})
                r.raise_for_status()
                return r.json()
        except Exception:
            return {"reply": "(agent error)", "intents": [], "entities": []}

_client = DeeppavlovClient(DEEPPAVLOV_URL)

def get_agent():
    return _client
