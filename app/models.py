from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class SmsSendRequest(BaseModel):
    to: str
    message: str

    model_config = ConfigDict(json_schema_extra={
        "example": {"to": "+4512345678", "message": "Hello from SIM7600!"}
    })


class HealthResponse(BaseModel):
    status: Literal["ok", "error"]
    port: str
    message: str


class StatusResponse(BaseModel):
    imsi: Optional[str]
    ccid: Optional[str]
    signal_strength: Optional[int]
    signal_dbm: Optional[float]
    network_registration: Optional[str]
    network_registration_text: str
    smsc: Optional[str]
    modem_port: str


class SmsMessage(BaseModel):
    index: int
    status: str
    sender: str
    timestamp: str
    message: str


class SmsListResponse(BaseModel):
    messages: list[SmsMessage]
    count: int


class SmsSendResponse(BaseModel):
    ok: bool
    message_reference: Optional[int]


class DeleteResponse(BaseModel):
    ok: bool
    deleted: str


class PinRequest(BaseModel):
    pin: str

    model_config = ConfigDict(json_schema_extra={"example": {"pin": "1234"}})


class PinStatusResponse(BaseModel):
    state: str
    description: str


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None


# ── Token / auth models ────────────────────────────────────────────────────────

class TokenStats(BaseModel):
    name: str
    request_count: int
    sms_sent: int
    last_used: Optional[str] = Field(None, description="ISO-8601 UTC timestamp of last request")


class TokenStatsResponse(BaseModel):
    auth_enabled: bool
    tokens: list[TokenStats]


class LogEntry(BaseModel):
    id: int
    timestamp: str
    token_name: str
    method: str
    endpoint: str
    status_code: int
    recipient: Optional[str] = None


class LogResponse(BaseModel):
    entries: list[LogEntry]
    total: int
    limit: int
    offset: int


# ── Debug models ───────────────────────────────────────────────────────────────

class AtCommandResponse(BaseModel):
    command: str
    response: str
    ok: bool


class PortInfo(BaseModel):
    port: str
    status: Literal["responsive", "no_response", "error"]
    response: Optional[str] = None
    error: Optional[str] = None


class PortScanResponse(BaseModel):
    ports: list[PortInfo]
