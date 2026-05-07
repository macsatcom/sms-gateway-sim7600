"""
Entry point that runs both the full API and the restricted API in the same
asyncio event loop, so they share a single serial port connection to the modem.

Full API     → API_PORT        (default 8000) — all endpoints
Restricted   → RESTRICTED_PORT (default 8007) — health, status, send only
"""
from __future__ import annotations

import asyncio
import os

import uvicorn

API_PORT        = int(os.environ.get("API_PORT",        "8000"))
RESTRICTED_PORT = int(os.environ.get("RESTRICTED_PORT", "8007"))


async def _serve() -> None:
    from main import app as full_app
    from restricted_app import app as restricted_app

    full_cfg = uvicorn.Config(
        full_app,
        host="0.0.0.0",
        port=API_PORT,
        log_level="info",
    )
    restricted_cfg = uvicorn.Config(
        restricted_app,
        host="0.0.0.0",
        port=RESTRICTED_PORT,
        log_level="info",
    )

    await asyncio.gather(
        uvicorn.Server(full_cfg).serve(),
        uvicorn.Server(restricted_cfg).serve(),
    )


if __name__ == "__main__":
    asyncio.run(_serve())
