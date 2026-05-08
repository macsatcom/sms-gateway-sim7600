# SMS Gateway — SIM7600E

Exposes SMS send/receive via a SIM7600E 4G USB modem as a local REST API with Swagger UI.

**Pipeline:** `HTTP request → FastAPI → pyserial → AT commands → SIM7600E → SMS network`

## Architecture

```
┌──────────────┐   HTTP :8000  ┌────────────────────────────┐   serial   ┌──────────────┐
│  HTTP client  │ ────────────▶ │  main.py (full API)         │ ─────────▶ │  SIM7600E    │
│  Swagger UI   │ ◀──────────── │  ModemManager (modem.py)   │ ◀───────── │  /dev/ttyUSB2│
│               │   HTTP :8007  │  restricted_app.py          │            └──────────────┘
│               │ ────────────▶ │  (health, status, send)    │
└──────────────┘               └────────────────────────────┘
```

Both apps run in the same asyncio event loop (`server.py`) sharing one serial port connection.

## Key Files

| File | Description |
|------|-------------|
| `app/server.py` | Entry point — runs full and restricted apps concurrently |
| `app/main.py` | FastAPI full app — all endpoints, lifespan (modem init) |
| `app/restricted_app.py` | Restricted API — health, status, send only |
| `app/modem.py` | `ModemManager` class — serial AT command layer, parsing |
| `app/models.py` | Pydantic request/response models |
| `app/state.py` | Shared state — modem instance, auth, request logger |
| `app/logger.py` | SQLite request log (persistent across restarts) |
| `docker/Dockerfile` | `python:3.12-slim`, installs fastapi/uvicorn/pyserial |
| `docker/docker-compose.yml` | Privileged container, ttyUSB device passthrough, healthcheck |
| `docker/.env.example` | All configuration variables with documentation |

## Configuration (env vars)

| Variable | Default | Description |
|----------|---------|-------------|
| `MODEM_PORT` | `/dev/ttyUSB2` | Serial device for AT commands |
| `MONITOR_PORT` | `/dev/ttyUSB7` | Secondary AT port for incoming SMS monitoring |
| `BAUD_RATE` | `115200` | Serial baud rate |
| `CMD_TIMEOUT` | `5.0` | Seconds to wait per AT command |
| `API_PORT` | `8000` | Full API HTTP port |
| `RESTRICTED_PORT` | `8007` | Restricted API port (health, status, send) |
| `API_KEYS` | *(empty)* | Named tokens: `name:token,name:token` |
| `API_KEY` | *(empty)* | Legacy single token (treated as name "default") |
| `SIM_PIN` | *(empty)* | Auto-unlock SIM on startup if set |
| `GPS_AUTOSTART` | `0` | Set to `1` to start GPS module on daemon startup |
| `LOG_DB` | `/data/sms-gateway.db` | SQLite request log path (survives restarts) |
| `DEBUG` | `0` | Set to `1` to log raw AT I/O to stdout |

## SIM7600E Port Layout

```
ttyUSB0 = Diagnostic    ttyUSB1 = GPS NMEA stream (raw, not used by gateway)
ttyUSB2 = AT commands ← default MODEM_PORT
ttyUSB3 = PPP modem     ttyUSB4 = ADB
ttyACM0 = CDC-NCM (network)
```

## SMS Encoding

All SMS sending/receiving uses **UCS-2 mode** (`AT+CSCS="UCS2"`). Message text is encoded
as UTF-16 BE hex strings in AT commands. This supports full Unicode including ÆØÅæøå and emoji.

- `AT+CMGS="<to_hex>"` — phone number encoded as UTF-16 BE hex
- Message body sent as UTF-16 BE hex string + Ctrl+Z
- Received messages decoded from UCS-2 hex back to Python strings

## API Endpoints

All endpoints except `GET /health` require `X-API-Key: <token>` header.

### Full API (`:8000`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Modem connectivity check (no auth) |
| GET | `/status` | IMSI, CCID, signal dBm, network registration |
| GET | `/sim/pin` | SIM PIN state |
| POST | `/sim/pin` | Submit PIN to unlock SIM |
| POST | `/sms/send` | Send SMS (single or multiple recipients) |
| GET | `/sms` | List stored SMS (`?status=REC UNREAD` optional) |
| GET | `/sms/stream` | SSE stream — push event for each incoming SMS |
| GET | `/sms/{index}` | Read one SMS by SIM storage index |
| DELETE | `/sms/{index}` | Delete one SMS by index |
| DELETE | `/sms` | Delete all SMS from SIM storage |
| GET | `/tokens` | List API token names and usage stats |
| GET | `/logs` | Paginated request log with filters |
| GET | `/gps/status` | GPS module on/off state |
| POST | `/gps/start` | Power on GNSS module |
| POST | `/gps/stop` | Power off GNSS module |
| GET | `/gps/location` | Current position (lat, lon, alt, speed, course) |
| GET | `/debug/at` | Send raw AT command (`?cmd=AT+CSQ`) |
| GET | `/debug/ports` | Probe ttyUSB0–7 for AT-responsive modems |
| GET | `/docs` | Swagger UI |

### Restricted API (`:8007`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Modem connectivity check (no auth) |
| GET | `/status` | SIM status (auth required) |
| POST | `/sms/send` | Send SMS (auth required) |

## `POST /sms/send` recipients

The `to` field accepts three forms:
- `"+4512345678"` — single number string
- `"+4512345678,+4687654321"` — comma-separated string
- `["+4512345678", "+4687654321"]` — JSON array

Returns a per-recipient result array; top-level `ok` is `true` only if all sends succeeded.

## Deploy

```bash
cd docker
cp .env.example .env
# Edit .env: set API_KEYS=myapp:strongtoken, verify MODEM_PORT
docker compose up --build
```

## Test API

```bash
KEY=your-api-key

# Health (no auth)
curl http://localhost:8000/health

# SIM status
curl -H "X-API-Key: $KEY" http://localhost:8000/status

# Send SMS (single recipient)
curl -X POST http://localhost:8000/sms/send \
  -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"to": "+4512345678", "message": "Hej! ÆØÅ works"}'

# Send SMS (multiple recipients)
curl -X POST http://localhost:8000/sms/send \
  -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"to": ["+4512345678", "+4687654321"], "message": "Hello all!"}'

# List all SMS
curl -H "X-API-Key: $KEY" http://localhost:8000/sms

# List unread only
curl -H "X-API-Key: $KEY" "http://localhost:8000/sms?status=REC%20UNREAD"

# Read message at index 1
curl -H "X-API-Key: $KEY" http://localhost:8000/sms/1

# Delete message at index 1
curl -X DELETE -H "X-API-Key: $KEY" http://localhost:8000/sms/1

# Delete all messages
curl -X DELETE -H "X-API-Key: $KEY" http://localhost:8000/sms

# Stream incoming SMS (Server-Sent Events)
curl -N -H "X-API-Key: $KEY" http://localhost:8000/sms/stream

# GPS
curl -H "X-API-Key: $KEY" -X POST http://localhost:8000/gps/start
curl -H "X-API-Key: $KEY" http://localhost:8000/gps/location

# Raw AT command
curl -H "X-API-Key: $KEY" "http://localhost:8000/debug/at?cmd=AT%2BCSQ"

# Swagger UI
open http://localhost:8000/docs
```

## Test modem layer directly (before Docker)

```bash
pip3 install pyserial
MODEM_PORT=/dev/ttyUSB2 python3 app/modem.py
```

## Confirm correct ttyUSB port

```bash
for p in /dev/ttyUSB{0..7}; do
  echo -n "$p: "
  python3 -c "import serial,time; s=serial.Serial('$p',115200,timeout=1); s.write(b'AT\r\n'); time.sleep(0.3); print(repr(s.read(64))); s.close()"
done
# The port responding with b'\r\nOK\r\n' is the AT command port
```

Or use `GET /debug/ports` after the gateway is running.

## Incoming SMS monitoring

The gateway opens `MONITOR_PORT` (default `ttyUSB7`) as a dedicated listen-only AT session.
It sets `AT+CNMI=2,1,0,0,0` on startup so the modem sends `+CMTI: "SM",<index>` URCs to that
port whenever an SMS arrives. The monitor reads the message via the main `MODEM_PORT` session
and broadcasts it to all connected SSE clients.

`GET /sms/stream` returns `text/event-stream`. Each event is a JSON object:

```
data: {"index": 3, "sender": "+4512345678", "message": "Hello!", "timestamp": "26/05/08,10:30:00+08", "status": "REC UNREAD"}
```

See `docs/ha-integration.md` for a complete Home Assistant integration guide.

## AT Commands Reference

| Command | Purpose |
|---------|---------|
| `AT+CMGF=1` | Text mode (set on startup) |
| `AT+CSCS="UCS2"` | UCS-2 charset — message body and phone numbers as UTF-16 BE hex |
| `AT+CPMS="SM","SM","SM"` | Use SIM card storage (set on startup) |
| `AT+CMGS="<num_hex>"` + body_hex + `\x1a` | Send SMS (phone and body as UCS-2 hex) |
| `AT+CMGL="ALL"` | List all stored SMS |
| `AT+CMGR=<n>` | Read SMS at index n |
| `AT+CMGD=<n>` | Delete SMS at index n |
| `AT+CMGD=1,4` | Delete all SMS |
| `AT+CSQ` | Signal quality (rssi, ber) |
| `AT+CIMI` | Read IMSI |
| `AT+CCID` | Read SIM card ID |
| `AT+CREG?` | Network registration status |
| `AT+CPIN?` | SIM PIN state |
| `AT+CGPS=1,1` | Start GPS in standalone mode |
| `AT+CGPS=0` | Stop GPS |
| `AT+CGPSINFO` | Current GPS position (NMEA-style fields) |
