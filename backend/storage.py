from __future__ import annotations
import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from .supabase_client import get_client
except Exception:  # pragma: no cover
    get_client = None  # type: ignore


def upload_recording(call_id: str, file_path: str, bucket: str = "recordings") -> Optional[str]:
    """Upload a recording file to Supabase Storage (if configured).

    Returns public URL on success, or None if client not available.
    """
    client = get_client()
    if not client:
        logger.warning("Supabase client not configured; cannot upload recording")
        return None
    try:
        # Use call-specific path
        import os

        filename = os.path.basename(file_path)
        dest_path = f"{call_id}/{filename}"
        # supabase-py storage API
        res = client.storage.from_(bucket).upload(dest_path, file_path)
        # If upload successful, build public URL
        public = client.storage.from_(bucket).get_public_url(dest_path)
        # get_public_url returns dict or object depending on lib version
        if isinstance(public, dict):
            return public.get("publicUrl") or public.get("public_url")
        return getattr(public, "public_url", None) or getattr(public, "publicUrl", None)
    except Exception as e:
        logger.exception("Failed to upload recording: %s", e)
        return None
