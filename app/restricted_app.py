from __future__ import annotations

from datetime import datetime, timezone

import state
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from modem import ModemError
from models import HealthResponse, SmsSendRequest, SmsSendResponse, SmsSendResult, StatusResponse

app = FastAPI(
    title="SMS Gateway — Restricted API",
    description=(
        "Limited SMS gateway: health check, SIM status, and send SMS.\n\n"
        "All endpoints except `/health` require the `X-API-Key` header."
    ),
    version="1.0.0",
)

_NO_LOG = frozenset({"/docs", "/openapi.json", "/redoc", "/favicon.ico"})


@app.middleware("http")
async def _log_requests(request: Request, call_next):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    response = await call_next(request)
    if request.url.path not in _NO_LOG:
        state.request_logger.log(
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
    dependencies=[Depends(state.require_api_key)],
)
def get_status():
    """Return IMSI, CCID, signal strength, and network registration state."""
    try:
        return state.modem.get_status()
    except ModemError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post(
    "/sms/send",
    response_model=SmsSendResponse,
    status_code=202,
    tags=["SMS"],
    summary="Send an SMS message",
)
def send_sms(request: Request, req: SmsSendRequest, _: str = Depends(state.require_api_key)):
    """
    Send an SMS to one or more recipients.

    `to` accepts a single number, a comma-separated string, or a JSON array.
    Returns a result entry per recipient.
    """
    request.state.recipient = ",".join(req.to)
    results: list[SmsSendResult] = []
    for number in req.to:
        try:
            mr = state.modem.send_sms(number, req.message)
            results.append(SmsSendResult(to=number, ok=True, message_reference=mr))
        except ModemError as e:
            results.append(SmsSendResult(to=number, ok=False, error=str(e)))
    return SmsSendResponse(ok=all(r.ok for r in results), results=results)
