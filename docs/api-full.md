# SMS Gateway — Full API Reference

The full API exposes all gateway capabilities: sending and reading SMS, SIM management,
GPS, token statistics, request logs, and modem debug tools.

**Default port:** `8000` (configure via `API_PORT` in `.env`)  
**Swagger UI:** `http://<host>:8000/docs`  
**Base URL:** `http://<host>:8000`

---

## Authentication

All endpoints except `GET /health` require the `X-API-Key` header.

### Configuring tokens

Tokens are set in `.env` before starting the container.

**Multiple named tokens (recommended):**

```env
API_KEYS=admin:mysecrettoken,monitoring:anothertoken,homeassistant:thirdtoken
```

Format is `name:token` pairs separated by commas. The name appears in usage logs and
token statistics — it is never exposed via the API.

**Single token (legacy):**

```env
API_KEY=mysecrettoken
```

Treated as a token named `default`.

**Generate a strong token:**

```bash
openssl rand -hex 32
```

### Using the key in requests

```http
X-API-Key: mysecrettoken
```

### Authentication errors

| Status | Meaning |
|--------|---------|
| `401` | Header missing or token invalid |

---

## Endpoints

### System

#### `GET /health`

Check whether the modem is connected and responsive. No authentication required.

```bash
curl http://localhost:8000/health
```

**Response `200`:**

```json
{
  "status": "ok",
  "port": "/dev/ttyUSB2",
  "message": "Modem responding to AT commands"
}
```

`status` is `"ok"` or `"error"`.

---

#### `GET /status`

Return IMSI, SIM card ID, signal strength, and network registration state.

```bash
curl -H "X-API-Key: $KEY" http://localhost:8000/status
```

**Response `200`:**

```json
{
  "imsi": "238010123456789",
  "ccid": "8945110000000000001",
  "signal_strength": 18,
  "signal_dbm": -75.0,
  "network_registration": "1",
  "network_registration_text": "Registered (home network)",
  "smsc": "+4540590000",
  "modem_port": "/dev/ttyUSB2"
}
```

`signal_dbm` is `null` if the modem reports no signal (`99`).

---

### SIM

#### `GET /sim/pin`

Check whether the SIM is unlocked, waiting for a PIN, or PUK-locked.

```bash
curl -H "X-API-Key: $KEY" http://localhost:8000/sim/pin
```

**Response `200`:**

```json
{
  "state": "READY",
  "description": "SIM is unlocked and ready"
}
```

Possible `state` values:

| Value | Meaning |
|-------|---------|
| `READY` | SIM unlocked and ready |
| `SIM PIN` | PIN required |
| `SIM PUK` | PUK-locked — contact your carrier |

---

#### `POST /sim/pin`

Submit the SIM PIN to unlock the card.

Only needed if `SIM_PIN` was not set in `.env` (automatic unlock on startup).

```bash
curl -X POST http://localhost:8000/sim/pin \
  -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"pin": "1234"}'
```

**Request body:**

```json
{
  "pin": "1234"
}
```

**Response `200`** — the new PIN state after entry:

```json
{
  "state": "READY",
  "description": "SIM is unlocked and ready"
}
```

**Response `400`** — wrong PIN or modem error.

---

### SMS

#### `POST /sms/send`

Send an SMS message to one or more recipients.

```bash
# Single recipient
curl -X POST http://localhost:8000/sms/send \
  -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"to": "+4512345678", "message": "Hello!"}'

# Multiple recipients (JSON array)
curl -X POST http://localhost:8000/sms/send \
  -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"to": ["+4512345678", "+4687654321"], "message": "Hello all!"}'
```

**Request body:**

```json
{
  "to": "+4512345678",
  "message": "Hello from the gateway!"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `to` | string \| array | Recipient(s) in international format. Accepts: `"+4512345678"`, `"+4512345678,+4687654321"`, or `["+4512345678", "+4687654321"]` |
| `message` | string | Message text. Full Unicode supported (ÆØÅæøå, emoji). Long messages are auto-split at 70 chars/segment (UCS-2) |

**Response `202`:**

```json
{
  "ok": true,
  "results": [
    {
      "to": "+4512345678",
      "ok": true,
      "message_reference": 42,
      "error": null
    }
  ]
}
```

Top-level `ok` is `true` only if **all** recipients succeeded. Each entry in `results` contains:

| Field | Description |
|-------|-------------|
| `to` | The recipient number |
| `ok` | Whether this individual send succeeded |
| `message_reference` | Modem-assigned delivery reference (`null` if unavailable) |
| `error` | Error message if `ok` is `false`, otherwise `null` |

---

#### `GET /sms`

List SMS messages stored on the SIM card.

```bash
# All messages
curl -H "X-API-Key: $KEY" http://localhost:8000/sms

# Unread messages only
curl -H "X-API-Key: $KEY" "http://localhost:8000/sms?status=REC%20UNREAD"
```

**Query parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `status` | *(all)* | Filter by status: `REC UNREAD`, `REC READ`, `STO UNSENT`, `STO SENT`. Omit for all. |

**Response `200`:**

```json
{
  "messages": [
    {
      "index": 1,
      "status": "REC UNREAD",
      "sender": "+4512345678",
      "timestamp": "26/05/07,10:23:15+08",
      "message": "Hello!"
    }
  ],
  "count": 1
}
```

`index` is the SIM storage slot (1-based). Use it with the read and delete endpoints.

---

#### `GET /sms/{index}`

Read a single SMS message by SIM storage index.

```bash
curl -H "X-API-Key: $KEY" http://localhost:8000/sms/1
```

**Path parameter:** `index` — integer ≥ 1.

**Response `200`:**

```json
{
  "index": 1,
  "status": "REC READ",
  "sender": "+4512345678",
  "timestamp": "26/05/07,10:23:15+08",
  "message": "Hello!"
}
```

**Response `404`** — no message at that index.

---

#### `DELETE /sms/{index}`

Delete a single SMS message by index.

```bash
curl -X DELETE -H "X-API-Key: $KEY" http://localhost:8000/sms/1
```

**Response `200`:**

```json
{
  "ok": true,
  "deleted": "index 1"
}
```

**Response `404`** — no message at that index.

---

#### `DELETE /sms`

Delete all SMS messages from SIM storage.

```bash
curl -X DELETE -H "X-API-Key: $KEY" http://localhost:8000/sms
```

**Response `200`:**

```json
{
  "ok": true,
  "deleted": "all"
}
```

---

#### `GET /sms/stream`

Open a persistent Server-Sent Events (SSE) connection. Each incoming SMS is pushed
as a JSON `data` event in real time.

Requires `MONITOR_PORT` to be configured (default `/dev/ttyUSB7`). The monitor listens
for `+CMTI` URCs on the secondary AT port and fetches the full message via the main port.

```bash
curl -N -H "X-API-Key: $KEY" http://localhost:8000/sms/stream
```

**Event format:**

```
data: {"index": 3, "sender": "+4512345678", "message": "Hello!", "timestamp": "26/05/08,10:30:00+08", "status": "REC UNREAD"}

: keepalive
```

A `: keepalive` comment is sent every 15 seconds to prevent the connection from timing out.

| Event field | Description |
|-------------|-------------|
| `index` | SIM storage index of the received message |
| `sender` | Sender phone number |
| `message` | Decoded message text |
| `timestamp` | Modem timestamp string (`YY/MM/DD,HH:MM:SS+tz`) |
| `status` | Always `"REC UNREAD"` for freshly received messages |

**Response `503`** — `MONITOR_PORT` is not configured or monitor failed to start.

---

### Tokens

#### `GET /tokens`

Return all configured token names and their aggregated usage statistics.

Token *values* are never exposed — only names.

```bash
curl -H "X-API-Key: $KEY" http://localhost:8000/tokens
```

**Response `200`:**

```json
{
  "auth_enabled": true,
  "tokens": [
    {
      "name": "admin",
      "request_count": 142,
      "sms_sent": 37,
      "last_used": "2026-05-07T09:14:22Z"
    },
    {
      "name": "homeassistant",
      "request_count": 511,
      "sms_sent": 204,
      "last_used": "2026-05-07T11:00:01Z"
    }
  ]
}
```

`auth_enabled` is `false` when no tokens are configured (open access mode).  
`last_used` is `null` if the token has never been used.

---

#### `GET /logs`

Query the full request log with optional filters. Results are newest-first.

```bash
# Last 100 entries
curl -H "X-API-Key: $KEY" http://localhost:8000/logs

# SMS sends by a specific token in a date range
curl -H "X-API-Key: $KEY" \
  "http://localhost:8000/logs?token=homeassistant&endpoint=/sms/send&since=2026-05-01T00:00:00Z&limit=50"
```

**Query parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `limit` | `100` | Max entries to return (1–1000) |
| `offset` | `0` | Entries to skip (pagination) |
| `token` | *(all)* | Filter by token name |
| `since` | *(all)* | ISO-8601 start timestamp, e.g. `2026-05-01T00:00:00Z` |
| `until` | *(all)* | ISO-8601 end timestamp, e.g. `2026-05-07T23:59:59Z` |
| `recipient` | *(all)* | Filter by recipient number (SMS sends only) |
| `endpoint` | *(all)* | Filter by endpoint path, e.g. `/sms/send` |

All filters are combinable.

**Response `200`:**

```json
{
  "entries": [
    {
      "id": 1024,
      "timestamp": "2026-05-07T11:00:01Z",
      "token_name": "homeassistant",
      "method": "POST",
      "endpoint": "/sms/send",
      "status_code": 202,
      "recipient": "+4512345678"
    }
  ],
  "total": 1024,
  "limit": 100,
  "offset": 0
}
```

`recipient` is only populated for `POST /sms/send` requests, `null` for all others.  
`total` reflects the count matching your filters, not the page size.

---

### GPS

The SIM7600E has a built-in GNSS module. It must be started explicitly (or via `GPS_AUTOSTART=1`)
and requires a clear sky view for a fix.

#### `GET /gps/status`

Return whether the GPS module is running.

```bash
curl -H "X-API-Key: $KEY" http://localhost:8000/gps/status
```

**Response `200`:**

```json
{
  "running": true,
  "streaming": false
}
```

---

#### `POST /gps/start`

Power on the GNSS module in standalone mode.

Allow 30–60 seconds outdoors for a first fix after starting.

```bash
curl -X POST -H "X-API-Key: $KEY" http://localhost:8000/gps/start
```

**Response `200`:** same shape as `GET /gps/status`.

**Response `503`** — modem error.

---

#### `POST /gps/stop`

Power off the GNSS module to save battery/power.

```bash
curl -X POST -H "X-API-Key: $KEY" http://localhost:8000/gps/stop
```

**Response `200`:** same shape as `GET /gps/status`.

---

#### `GET /gps/location`

Return the current position from the GNSS module.

```bash
curl -H "X-API-Key: $KEY" http://localhost:8000/gps/location
```

**Response `200` (fix acquired):**

```json
{
  "fix": true,
  "latitude": 55.6761,
  "longitude": 12.5683,
  "altitude_m": 14.2,
  "speed_kmh": 0.0,
  "course_deg": 0.0,
  "hdop": 1.2,
  "satellites_in_view": 8,
  "utc_datetime": "2026-05-07T11:00:00Z"
}
```

**Response `200` (no fix yet):**

```json
{
  "fix": false,
  "latitude": null,
  "longitude": null,
  "altitude_m": null,
  "speed_kmh": null,
  "course_deg": null,
  "hdop": null,
  "satellites_in_view": null,
  "utc_datetime": null
}
```

**Response `503`** — GPS module is not running.

---

### Debug

These endpoints give direct access to the modem. Treat them as privileged — restrict
access to trusted operators.

#### `GET /debug/at`

Send a raw AT command to the modem and return the response.

```bash
curl -H "X-API-Key: $KEY" "http://localhost:8000/debug/at?cmd=AT%2BCSQ"
```

**Query parameter:** `cmd` — AT command to send, e.g. `AT+CSQ`.

**Response `200`:**

```json
{
  "command": "AT+CSQ",
  "response": "+CSQ: 18,0\r\n\r\nOK",
  "ok": true
}
```

`ok` is `false` (not an HTTP error) if the modem returns an error response.

---

#### `GET /debug/ports`

Probe `/dev/ttyUSB0`–`7` and report which ports respond to AT commands.

Useful for identifying the correct `MODEM_PORT` value.

```bash
curl -H "X-API-Key: $KEY" http://localhost:8000/debug/ports
```

**Response `200`:**

```json
{
  "ports": [
    { "port": "/dev/ttyUSB0", "status": "no_response", "response": null, "error": null },
    { "port": "/dev/ttyUSB1", "status": "no_response", "response": null, "error": null },
    { "port": "/dev/ttyUSB2", "status": "responsive", "response": "\r\nOK\r\n", "error": null },
    { "port": "/dev/ttyUSB3", "status": "error", "response": null, "error": "Permission denied" }
  ]
}
```

`status` values: `responsive`, `no_response`, `error`.  
Ports not passed through to the Docker container will show `"error"`.

---

## Error format

All errors return JSON:

```json
{
  "error": "Internal server error",
  "detail": "Serial timeout on /dev/ttyUSB2"
}
```

Common HTTP status codes:

| Status | Meaning |
|--------|---------|
| `400` | Bad request (e.g. wrong PIN) |
| `401` | Missing or invalid `X-API-Key` |
| `404` | Resource not found (e.g. SMS index does not exist) |
| `422` | Validation error (missing or malformed request body field) |
| `502` | Modem failed to complete the operation |
| `503` | Modem unavailable or GPS not running |
| `500` | Unexpected internal error |

---

## Quick-start example (Python)

```python
import httpx

BASE = "http://localhost:8000"
KEY  = "mysecrettoken"
HEADERS = {"X-API-Key": KEY}

# Health check (no auth)
r = httpx.get(f"{BASE}/health")
print(r.json())  # {"status": "ok", ...}

# Send an SMS
r = httpx.post(
    f"{BASE}/sms/send",
    headers=HEADERS,
    json={"to": "+4512345678", "message": "Hello!"},
)
print(r.json())  # {"ok": true, "message_reference": 42}

# List unread SMS
r = httpx.get(f"{BASE}/sms", headers=HEADERS, params={"status": "REC UNREAD"})
print(r.json())
```

---

## Configuration reference

All settings live in `docker/.env` (copy from `docker/.env.example`).

| Variable | Default | Description |
|----------|---------|-------------|
| `MODEM_PORT` | `/dev/ttyUSB2` | Serial device for AT commands |
| `MONITOR_PORT` | `/dev/ttyUSB7` | Secondary AT port for incoming SMS monitoring (`GET /sms/stream`) |
| `BAUD_RATE` | `115200` | Serial baud rate |
| `CMD_TIMEOUT` | `5.0` | Seconds to wait per AT command. Increase to `30.0` for slow carriers |
| `API_PORT` | `8000` | Port for the full API |
| `RESTRICTED_PORT` | `8007` | Port for the restricted API (health, status, send only) |
| `API_KEYS` | *(empty)* | Named tokens: `name:token,name:token` |
| `API_KEY` | *(empty)* | Legacy single token (name `"default"`) |
| `SIM_PIN` | *(empty)* | SIM PIN for automatic unlock on startup |
| `GPS_AUTOSTART` | `0` | Set to `1` to start GPS on daemon startup |
| `LOG_DB` | `/data/sms-gateway.db` | Path to the SQLite request log (inside container) |
| `DEBUG` | `0` | Set to `1` to log raw AT I/O to stdout |
