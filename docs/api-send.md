# SMS Gateway — Send API Reference

The Send API is a restricted endpoint surface intended for external integrations that
only need to send SMS — for example Home Assistant, monitoring systems, or CI pipelines.
It exposes three endpoints and nothing else: health check, SIM status, and send SMS.

Because it carries no read, delete, token management, or debug functionality, it is
safe to expose externally via a router port-forward or reverse proxy.

**Default port:** `8007` (configure via `RESTRICTED_PORT` in `.env`)  
**Swagger UI:** `http://<host>:8007/docs`  
**Base URL:** `http://<host>:8007`

---

## Authentication

All endpoints except `GET /health` require the `X-API-Key` header.

```http
X-API-Key: mysecrettoken
```

Tokens are shared with the full API — the same key works on both ports.
Configure tokens in `docker/.env`:

```env
# Multiple named tokens (recommended)
API_KEYS=homeassistant:mytoken,monitoring:anothertoken

# Or a single token
API_KEY=mytoken
```

Generate a strong token:

```bash
openssl rand -hex 32
```

If no token is configured, authentication is disabled on both ports (open access mode).

### Authentication errors

| Status | Meaning |
|--------|---------|
| `401` | Header missing or token invalid |

---

## Endpoints

### `GET /health`

Check whether the modem is connected and responsive. No authentication required.

Useful as a liveness probe or uptime check.

```bash
curl http://localhost:8007/health
```

**Response `200`:**

```json
{
  "status": "ok",
  "port": "/dev/ttyUSB2",
  "message": "Modem responding to AT commands"
}
```

`status` is `"ok"` or `"error"`. An `"error"` response still returns HTTP `200` — check
the `status` field in your client, not just the HTTP status code.

---

### `GET /status`

Return SIM and network information: IMSI, SIM card ID, signal strength, and
network registration state.

```bash
curl -H "X-API-Key: $KEY" http://localhost:8007/status
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

| Field | Type | Description |
|-------|------|-------------|
| `imsi` | string\|null | SIM identity number |
| `ccid` | string\|null | SIM card serial number |
| `signal_strength` | int\|null | Raw RSSI value (0–31, 99 = unknown) |
| `signal_dbm` | float\|null | Signal in dBm. `null` if modem reports no signal |
| `network_registration` | string\|null | Raw registration code from `AT+CREG` |
| `network_registration_text` | string | Human-readable registration state |
| `smsc` | string\|null | SMS Service Centre number |
| `modem_port` | string | Serial port in use |

**Response `503`** — modem not responding.

---

### `POST /sms/send`

Send an SMS message to any number.

```bash
curl -X POST http://localhost:8007/sms/send \
  -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"to": "+4512345678", "message": "Hello!"}'
```

**Request body:**

```json
{
  "to": "+4512345678",
  "message": "Hello from the gateway!"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `to` | string | yes | Recipient phone number in international format, e.g. `+4512345678` |
| `message` | string | yes | Message text. Messages longer than 160 characters are automatically split by the modem (concatenated/long SMS) |

**Response `202`:**

```json
{
  "ok": true,
  "message_reference": 42
}
```

`ok: true` means the modem accepted the message and passed it to the network.
`message_reference` is the modem-assigned delivery reference number (`null` if unavailable).

**Response `401`** — missing or invalid API key.  
**Response `422`** — missing or malformed request body field.  
**Response `502`** — modem failed to send (no signal, SIM not ready, etc.).

---

## Error format

All errors return JSON:

```json
{
  "error": "Internal server error",
  "detail": "Serial timeout on /dev/ttyUSB2"
}
```

---

## Quick-start examples

### curl

```bash
KEY=mysecrettoken

curl -X POST http://localhost:8007/sms/send \
  -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"to": "+4512345678", "message": "Alert: disk usage above 90%"}'
```

### Python

```python
import httpx

r = httpx.post(
    "http://localhost:8007/sms/send",
    headers={"X-API-Key": "mysecrettoken"},
    json={"to": "+4512345678", "message": "Alert: disk usage above 90%"},
)
r.raise_for_status()
print(r.json())  # {"ok": true, "message_reference": 42}
```

### Home Assistant REST command

```yaml
# configuration.yaml
rest_command:
  send_sms:
    url: "http://192.168.1.100:8007/sms/send"
    method: POST
    headers:
      X-API-Key: "mysecrettoken"
      Content-Type: "application/json"
    payload: '{"to": "{{ to }}", "message": "{{ message }}"}'
```

```yaml
# automation
action:
  - service: rest_command.send_sms
    data:
      to: "+4512345678"
      message: "Front door opened"
```

### Node.js / fetch

```js
await fetch("http://localhost:8007/sms/send", {
  method: "POST",
  headers: {
    "X-API-Key": "mysecrettoken",
    "Content-Type": "application/json",
  },
  body: JSON.stringify({ to: "+4512345678", message: "Hello!" }),
});
```

---

## Exposing externally

The Send API is designed to be safe for external exposure. It has no endpoints that
reveal stored messages, token values, logs, or modem internals.

Example Nginx reverse-proxy config:

```nginx
location /sms/ {
    proxy_pass http://127.0.0.1:8007;
    proxy_set_header Host $host;
}
```

The full API (port `8000`) should remain on your internal network only.
