from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import state
from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse
from state import TOKENS, request_logger, require_api_key

from modem import BAUD_RATE, CMD_TIMEOUT, MODEM_PORT, ModemError, ModemManager
from models import (
    AtCommandResponse,
    DeleteResponse,
    HealthResponse,
    LogEntry,
    LogResponse,
    PinRequest,
    PinStatusResponse,
    PortScanResponse,
    SmsListResponse,
    SmsMessage,
    SmsSendRequest,
    SmsSendResponse,
    StatusResponse,
    TokenStats,
    TokenStatsResponse,
)

# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    state.modem = ModemManager(MODEM_PORT, BAUD_RATE, CMD_TIMEOUT)
    try:
        state.modem.open()
        print(f"[sms-gateway] Modem initialised on {MODEM_PORT}")
    except Exception as e:
        print(f"[sms-gateway] WARNING: Modem init failed: {e}")
    yield
    if state.modem._serial and state.modem._serial.is_open:
        state.modem._serial.close()
        print("[sms-gateway] Serial port closed")


# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="SMS Gateway — SIM7600E",
    description=(
        "REST API for sending and receiving SMS via a SIM7600E 4G modem.\n\n"
        "All endpoints except `/health` require the `X-API-Key` header."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


_NO_LOG = frozenset({"/docs", "/openapi.json", "/redoc", "/favicon.ico"})


@app.middleware("http")
async def _log_requests(request: Request, call_next):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    response = await call_next(request)
    if request.url.path not in _NO_LOG:
        request_logger.log(
            timestamp=timestamp,
            token_name=getattr(request.state, "token_name", "anonymous"),
            method=request.method,
            endpoint=request.url.path,
            status_code=response.status_code,
            recipient=getattr(request.state, "recipient", None),
        )
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": str(exc)},
    )


_PIN_STATE_TEXT = {
    "READY":   "SIM is unlocked and ready",
    "SIM PIN": "SIM PIN required",
    "SIM PUK": "SIM is PUK-locked — contact your carrier",
}

# ── System endpoints ───────────────────────────────────────────────────────────

@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["System"],
    summary="Modem health check",
)
def get_health():
    """Check whether the modem is connected and responsive. No auth required."""
    return state.modem.check_health()


@app.get(
    "/status",
    response_model=StatusResponse,
    tags=["System"],
    summary="SIM and network status",
    dependencies=[Depends(require_api_key)],
)
def get_status():
    """Return IMSI, CCID, signal strength, and network registration state."""
    try:
        return state.modem.get_status()
    except ModemError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get(
    "/sim/pin",
    response_model=PinStatusResponse,
    tags=["SIM"],
    summary="Get SIM PIN status",
    dependencies=[Depends(require_api_key)],
)
def get_pin_status():
    """Check whether the SIM card is unlocked, waiting for a PIN, or PUK-locked."""
    try:
        pin_state = state.modem.get_pin_state()
        return {"state": pin_state, "description": _PIN_STATE_TEXT.get(pin_state, pin_state)}
    except ModemError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post(
    "/sim/pin",
    response_model=PinStatusResponse,
    tags=["SIM"],
    summary="Submit SIM PIN",
    dependencies=[Depends(require_api_key)],
)
def submit_pin(req: PinRequest):
    """
    Unlock the SIM by submitting its PIN code.

    Only needed if the SIM was not automatically unlocked at startup
    (i.e. SIM_PIN env var was not set). Returns the new PIN state after entry.
    """
    try:
        state.modem.enter_pin(req.pin)
        pin_state = state.modem.get_pin_state()
        return {"state": pin_state, "description": _PIN_STATE_TEXT.get(pin_state, pin_state)}
    except ModemError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── SMS endpoints ──────────────────────────────────────────────────────────────

@app.post(
    "/sms/send",
    response_model=SmsSendResponse,
    status_code=202,
    tags=["SMS"],
    summary="Send an SMS message",
)
def send_sms(request: Request, req: SmsSendRequest, _: str = Depends(require_api_key)):
    """
    Send an SMS message to the given number.

    Messages longer than 160 characters are automatically split into multiple
    parts by the modem (concatenated SMS / long SMS).
    """
    request.state.recipient = req.to
    try:
        mr = state.modem.send_sms(req.to, req.message)
        return {"ok": True, "message_reference": mr}
    except ModemError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get(
    "/sms",
    response_model=SmsListResponse,
    tags=["SMS"],
    summary="List stored SMS messages",
    dependencies=[Depends(require_api_key)],
)
def list_sms(
    status: Optional[str] = Query(
        default=None,
        description=(
            'Filter by message status. '
            'Values: "REC UNREAD", "REC READ", "STO UNSENT", "STO SENT". '
            'Omit to return all messages.'
        ),
        example="REC UNREAD",
    )
):
    """List all SMS messages stored on the SIM card."""
    try:
        messages = state.modem.list_sms(status or "ALL")
        return {"messages": messages, "count": len(messages)}
    except ModemError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get(
    "/sms/{index}",
    response_model=SmsMessage,
    tags=["SMS"],
    summary="Read a single SMS message",
    dependencies=[Depends(require_api_key)],
)
def read_sms(
    index: int = Path(ge=1, description="SMS storage index (1-based)")
):
    """Read one SMS message by its SIM storage index."""
    try:
        return state.modem.read_sms(index)
    except ModemError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete(
    "/sms/{index}",
    response_model=DeleteResponse,
    tags=["SMS"],
    summary="Delete a single SMS message",
    dependencies=[Depends(require_api_key)],
)
def delete_sms(
    index: int = Path(ge=1, description="SMS storage index (1-based)")
):
    """Delete one SMS message by its SIM storage index."""
    try:
        state.modem.delete_sms(index)
        return {"ok": True, "deleted": f"index {index}"}
    except ModemError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete(
    "/sms",
    response_model=DeleteResponse,
    tags=["SMS"],
    summary="Delete all SMS messages",
    dependencies=[Depends(require_api_key)],
)
def delete_all_sms():
    """Delete all SMS messages from SIM card storage."""
    try:
        state.modem.delete_all_sms()
        return {"ok": True, "deleted": "all"}
    except ModemError as e:
        raise HTTPException(status_code=503, detail=str(e))


# ── Token / stats endpoints ────────────────────────────────────────────────────

@app.get(
    "/tokens",
    response_model=TokenStatsResponse,
    tags=["Tokens"],
    summary="List API tokens and usage statistics",
    dependencies=[Depends(require_api_key)],
)
def list_tokens():
    """
    Return all configured token names with aggregated usage from the persistent log.

    Includes total request count, SMS messages sent, and last-used timestamp.
    Token values are never exposed.
    """
    stats = request_logger.get_token_stats(list(set(TOKENS.values())))
    return {
        "auth_enabled": bool(TOKENS),
        "tokens": [TokenStats(**s) for s in stats],
    }


@app.get(
    "/logs",
    response_model=LogResponse,
    tags=["Tokens"],
    summary="Query the full request log",
    dependencies=[Depends(require_api_key)],
)
def get_logs(
    limit: int = Query(100, ge=1, le=1000, description="Max entries to return"),
    offset: int = Query(0, ge=0, description="Entries to skip (for pagination)"),
    token: Optional[str] = Query(None, description="Filter by token name"),
    since: Optional[str] = Query(None, description="ISO-8601 start timestamp, e.g. 2026-05-01T00:00:00Z"),
    until: Optional[str] = Query(None, description="ISO-8601 end timestamp, e.g. 2026-05-07T23:59:59Z"),
    recipient: Optional[str] = Query(None, description="Filter by recipient number (SMS sends only)"),
    endpoint: Optional[str] = Query(None, description="Filter by API endpoint path, e.g. /sms/send"),
):
    """
    Return paginated request log entries with optional filters.

    All filters are combinable. Results are ordered newest-first.
    """
    entries, total = request_logger.get_logs(
        limit=limit,
        offset=offset,
        token_name=token,
        since=since,
        until=until,
        recipient=recipient,
        endpoint=endpoint,
    )
    return {
        "entries": [LogEntry(**e) for e in entries],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ── Debug endpoints ────────────────────────────────────────────────────────────

@app.get(
    "/debug/at",
    response_model=AtCommandResponse,
    tags=["Debug"],
    summary="Send a raw AT command",
    dependencies=[Depends(require_api_key)],
)
def debug_at(
    cmd: str = Query(..., description="AT command to send (e.g. AT+CSQ)", example="AT+CSQ"),
):
    """
    Send an arbitrary AT command to the modem and return the raw response.

    Useful for diagnosing connection issues or querying modem state directly.
    Note: this endpoint has full modem access — treat it as privileged.
    """
    try:
        response = state.modem._send_command(cmd)
        return {"command": cmd, "response": response, "ok": True}
    except ModemError as e:
        return {"command": cmd, "response": str(e), "ok": False}


@app.get(
    "/debug/ports",
    response_model=PortScanResponse,
    tags=["Debug"],
    summary="Scan ttyUSB ports for AT-responsive modems",
    dependencies=[Depends(require_api_key)],
)
def debug_ports():
    """
    Probe /dev/ttyUSB0–7 and report which ports respond to AT commands.

    Ports not passed through to the Docker container will appear as errors.
    To expose additional ports, add them to `devices:` in docker-compose.yml.
    """
    results = ModemManager.scan_ports(BAUD_RATE)
    ports = []
    for r in results:
        ports.append({
            "port": r["port"],
            "status": r["status"],
            "response": r.get("response"),
            "error": r.get("error"),
        })
    return {"ports": ports}
