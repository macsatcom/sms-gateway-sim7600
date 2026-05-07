# SMS Gateway — SIM7600E

Exposes SMS send/receive via a SIM7600E 4G USB modem as a local REST API with Swagger UI.

**Pipeline:** `HTTP request → FastAPI → pyserial → AT commands → SIM7600E → SMS network`

## Architecture

```
┌──────────────┐   HTTP    ┌────────────────────────────┐   serial   ┌──────────────┐
│  HTTP client  │ ────────▶ │  FastAPI (main.py)          │ ─────────▶ │  SIM7600E    │
│  Swagger UI   │ ◀──────── │  ModemManager (modem.py)   │ ◀───────── │  /dev/ttyUSB2│
└──────────────┘           └────────────────────────────┘            └──────────────┘
```

## Key Files

| File | Description |
|------|-------------|
| `app/main.py` | FastAPI app, lifespan handler, auth dependency, all endpoints |
| `app/modem.py` | `ModemManager` class — serial AT command layer, parsing |
| `app/models.py` | Pydantic request/response models (defines the API contract) |
| `docker/Dockerfile` | `python:3.12-slim`, installs fastapi/uvicorn/pyserial |
| `docker/docker-compose.yml` | Privileged container, ttyUSB device passthrough, healthcheck |
| `docker/.env.example` | Template for MODEM_PORT, API_KEY, etc. |

## Configuration (env vars)

| Variable | Default | Description |
|----------|---------|-------------|
| `MODEM_PORT` | `/dev/ttyUSB2` | Serial device for AT commands |
| `BAUD_RATE` | `115200` | Serial baud rate |
| `CMD_TIMEOUT` | `5.0` | Seconds to wait per AT command |
| `API_PORT` | `8000` | HTTP port for the REST API |
| `API_KEY` | *(empty)* | Auth key for `X-API-Key` header. Empty = no auth |

## SIM7600E Port Layout

```
ttyUSB0 = Diagnostic    ttyUSB1 = GPS NMEA
ttyUSB2 = AT commands ← default MODEM_PORT
ttyUSB3 = PPP modem     ttyUSB4 = ADB
ttyACM0 = CDC-NCM (network)
```

## API Endpoints

All endpoints except `GET /health` require `X-API-Key: <key>` header.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Modem connectivity check (no auth) |
| GET | `/status` | IMSI, CCID, signal dBm, network registration |
| POST | `/sms/send` | Send SMS `{"to": "+45...", "message": "..."}` |
| GET | `/sms` | List stored SMS (`?status=REC+UNREAD` optional) |
| GET | `/sms/{index}` | Read one SMS by SIM storage index |
| DELETE | `/sms/{index}` | Delete one SMS by index |
| DELETE | `/sms` | Delete all SMS from SIM storage |
| GET | `/docs` | Swagger UI (interactive API explorer) |

## Deploy

```bash
cd docker
cp .env.example .env
# Edit .env: set API_KEY=<strong-key>, verify MODEM_PORT
docker compose up --build
```

## Test API

```bash
KEY=your-api-key

# Health (no auth)
curl http://localhost:8000/health

# SIM status
curl -H "X-API-Key: $KEY" http://localhost:8000/status

# Send SMS
curl -X POST http://localhost:8000/sms/send \
  -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"to": "+4512345678", "message": "Hello!"}'

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

## AT Commands Reference

| Command | Purpose |
|---------|---------|
| `AT+CMGF=1` | Text mode (set on startup) |
| `AT+CPMS="SM","SM","SM"` | Use SIM card storage (set on startup) |
| `AT+CMGS="<num>"` + body + `\x1a` | Send SMS |
| `AT+CMGL="ALL"` | List all stored SMS |
| `AT+CMGR=<n>` | Read SMS at index n |
| `AT+CMGD=<n>` | Delete SMS at index n |
| `AT+CMGD=1,4` | Delete all SMS |
| `AT+CSQ` | Signal quality (rssi, ber) |
| `AT+CIMI` | Read IMSI |
| `AT+CCID` | Read SIM card ID |
| `AT+CREG?` | Network registration status |
