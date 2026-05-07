from __future__ import annotations

import os
from typing import Optional

from fastapi import Header, HTTPException, Request

from logger import DB_PATH, RequestLogger
from modem import ModemManager

# Shared modem instance — set by main.py lifespan, read by both apps.
# Both FastAPI apps run in the same asyncio event loop (server.py), so this
# module is loaded once and the reference is shared safely.
modem: Optional[ModemManager] = None

# ── Auth ───────────────────────────────────────────────────────────────────────

def _load_tokens() -> dict[str, str]:
    """
    Parse named API tokens from env vars.

    API_KEYS=admin:token1,monitoring:token2   (preferred — multiple named tokens)
    API_KEY=token                             (legacy — treated as name "default")

    Returns {token_value: name} for O(1) lookup.
    """
    raw = os.environ.get("API_KEYS", "").strip()
    if raw:
        result: dict[str, str] = {}
        for pair in raw.split(","):
            pair = pair.strip()
            if ":" in pair:
                name, _, token = pair.partition(":")
                name, token = name.strip(), token.strip()
                if name and token:
                    result[token] = name
        return result
    key = os.environ.get("API_KEY", "").strip()
    if key:
        return {key: "default"}
    return {}


TOKENS: dict[str, str] = _load_tokens()


def require_api_key(request: Request, x_api_key: Optional[str] = Header(None)) -> str:
    """Validate X-API-Key, store token name on request state. Returns token name."""
    if not TOKENS:
        request.state.token_name = "anonymous"
        return "anonymous"
    if not x_api_key:
        raise HTTPException(status_code=401, detail="X-API-Key header required")
    name = TOKENS.get(x_api_key)
    if name is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    request.state.token_name = name
    return name


# ── Request logger ─────────────────────────────────────────────────────────────
request_logger = RequestLogger(DB_PATH)
