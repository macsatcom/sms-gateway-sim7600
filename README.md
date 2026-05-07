# SMS Gateway — SIM7600E

[![Docker Image](https://ghcr-badge.egress.umami.is/macsatcom/sms-gateway-sim7600/latest_tag?label=docker)](https://github.com/macsatcom/sms-gateway-sim7600/pkgs/container/sms-gateway-sim7600)
[![Build](https://github.com/macsatcom/sms-gateway-sim7600/actions/workflows/docker.yml/badge.svg)](https://github.com/macsatcom/sms-gateway-sim7600/actions)

A self-hosted SMS gateway that exposes a SIM7600E 4G USB modem as a local REST API with Swagger UI. Send and receive SMS from any HTTP client on your network — no cloud service required.

**Pipeline:** `HTTP client → FastAPI → pyserial → AT commands → SIM7600E → SMS network`

---

## Features

- Send and receive SMS via a simple REST API
- Swagger UI at `/docs` — try every endpoint in the browser
- Multiple named API keys with per-token usage statistics
- Persistent request log (SQLite) queryable via API
- SIM PIN unlock on startup
- Raw AT command passthrough for debugging
- USB port scanner to identify the correct serial interface
- Zero-dependency CLI client (`sms-cli.py`) — uses only the Python standard library
- Multi-arch Docker image (`linux/amd64`, `linux/arm64`) — runs on Raspberry Pi

---

## Hardware

- SIM7600E 4G HAT or USB dongle
- Active SIM card with SMS capability
- Linux host with USB access (x86 server, Raspberry Pi, etc.)

---

## Quick Start

**1. Create a configuration file:**

```bash
curl -o .env https://raw.githubusercontent.com/macsatcom/sms-gateway-sim7600/main/docker/.env.example
```

Open `.env` and set at minimum:

```bash
API_KEYS=admin:your-secret-key    # generate with: openssl rand -hex 32
MODEM_PORT=/dev/ttyUSB2           # see "Finding the right port" below
```

**2. Create a `docker-compose.yml`:**

```yaml
services:
  sms-gateway:
    image: ghcr.io/macsatcom/sms-gateway-sim7600:latest
    devices:
      - ${MODEM_PORT:-/dev/ttyUSB2}
    ports:
      - "${API_PORT:-8000}:${API_PORT:-8000}"
    volumes:
      - sms_data:/data
    env_file: .env
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:${API_PORT:-8000}/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s

volumes:
  sms_data:
```

**3. Start:**

```bash
docker compose up -d
```

The API is ready when `GET /health` returns `{"status": "ok", ...}`.

Open **http://localhost:8000/docs** for the interactive Swagger UI.

---

## Configuration

All settings go in `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `MODEM_PORT` | `/dev/ttyUSB2` | Serial device for AT commands |
| `BAUD_RATE` | `115200` | Serial baud rate |
| `CMD_TIMEOUT` | `5.0` | Seconds to wait per AT command. Increase to `30.0` if your carrier is slow. |
| `API_PORT` | `8000` | Host port to expose the API on |
| `API_KEYS` | *(empty)* | Named API keys — see below |
| `API_KEY` | *(empty)* | Legacy single key (used if `API_KEYS` is empty) |
| `SIM_PIN` | *(empty)* | SIM PIN code — gateway unlocks the SIM automatically on startup |
| `LOG_DB` | `/data/sms-gateway.db` | Path to the SQLite request log (inside container) |
| `DEBUG` | `0` | Set to `1` to log raw AT command I/O to stdout |

### Multiple API Keys

`API_KEYS` takes a comma-separated list of `name:token` pairs. Names appear in `/tokens` and `/logs` so you can see which caller made each request.

```bash
# In .env:
API_KEYS=admin:supersecrettoken,homeassistant:anothertoken,ci:thirdtoken
```

Generate strong tokens with:
```bash
openssl rand -hex 32
```

If both `API_KEYS` and `API_KEY` are empty, authentication is disabled entirely.

---

## API Reference

All endpoints except `GET /health` require the header:
```
X-API-Key: your-token
```

### System

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | No | Modem connectivity check |
| GET | `/status` | Yes | IMSI, CCID, signal dBm, network registration |
| GET | `/docs` | No | Swagger UI |

#### `GET /health`
```bash
curl http://localhost:8000/health
```
```json
{"status": "ok", "port": "/dev/ttyUSB2", "message": "Modem responsive"}
```

#### `GET /status`
```bash
curl -H "X-API-Key: $KEY" http://localhost:8000/status
```
```json
{
  "imsi": "238010123456789",
  "ccid": "89450121180216254762",
  "signal_strength": 18,
  "signal_dbm": -77.0,
  "network_registration": "1",
  "network_registration_text": "Registered, home network",
  "smsc": "+4540590000",
  "modem_port": "/dev/ttyUSB2"
}
```

---

### SMS

| Method | Path | Description |
|--------|------|-------------|
| POST | `/sms/send` | Send an SMS |
| GET | `/sms` | List stored SMS (`?status=REC+UNREAD` optional) |
| GET | `/sms/{index}` | Read one SMS by SIM storage index |
| DELETE | `/sms/{index}` | Delete one SMS by index |
| DELETE | `/sms` | Delete all SMS from SIM storage |

#### `POST /sms/send`
Returns HTTP 202 when the modem has accepted the message. Messages longer than 160 characters are automatically split.

```bash
curl -X POST http://localhost:8000/sms/send \
  -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"to": "+4512345678", "message": "Hello from the gateway!"}'
```
```json
{"ok": true, "message_reference": 7}
```

#### `GET /sms`
```bash
curl -H "X-API-Key: $KEY" http://localhost:8000/sms
curl -H "X-API-Key: $KEY" "http://localhost:8000/sms?status=REC%20UNREAD"
```

Valid `status` values: `REC UNREAD`, `REC READ`, `STO UNSENT`, `STO SENT`

```json
{
  "messages": [
    {
      "index": 1,
      "status": "REC UNREAD",
      "sender": "+4598765432",
      "timestamp": "26/05/07,09:30:00+08",
      "message": "Hey, got your message!"
    }
  ],
  "count": 1
}
```

---

### SIM

| Method | Path | Description |
|--------|------|-------------|
| GET | `/sim/pin` | Check SIM PIN state (READY / SIM PIN / SIM PUK) |
| POST | `/sim/pin` | Unlock SIM with PIN `{"pin": "1234"}` |

---

### Tokens & Logs

| Method | Path | Description |
|--------|------|-------------|
| GET | `/tokens` | List token names with request counts and last-used timestamps |
| GET | `/logs` | Query the full request log (filterable, paginated) |

#### `GET /tokens`
```bash
curl -H "X-API-Key: $KEY" http://localhost:8000/tokens
```
```json
{
  "auth_enabled": true,
  "tokens": [
    {"name": "admin", "request_count": 42, "sms_sent": 15, "last_used": "2026-05-07T09:30:00Z"},
    {"name": "homeassistant", "request_count": 120, "sms_sent": 87, "last_used": "2026-05-07T08:00:00Z"}
  ]
}
```

#### `GET /logs`
```bash
# Last 50 requests
curl -H "X-API-Key: $KEY" http://localhost:8000/logs

# Filter by token and time range
curl -H "X-API-Key: $KEY" \
  "http://localhost:8000/logs?token=homeassistant&since=2026-05-01T00:00:00Z&limit=100"

# Filter by recipient
curl -H "X-API-Key: $KEY" \
  "http://localhost:8000/logs?endpoint=/sms/send&recipient=%2B4512345678"
```

Query parameters: `limit`, `offset`, `token`, `since`, `until`, `recipient`, `endpoint`

---

### Debug

| Method | Path | Description |
|--------|------|-------------|
| GET | `/debug/at?cmd=AT+CSQ` | Send a raw AT command, get the raw response |
| GET | `/debug/ports` | Scan `/dev/ttyUSB0–7` and report which respond to AT |

---

## CLI Client

`sms-cli.py` is a zero-dependency command-line client (pure Python standard library).

**Token lookup order:**
1. `--key` / `-k` flag
2. `SMS_GATEWAY_KEY` environment variable
3. `.token` file next to the script

```bash
# Put your key in .token once, then forget about it
echo "your-api-key" > .token

# Usage
python3 sms-cli.py health
python3 sms-cli.py status
python3 sms-cli.py send +4512345678 "Hello!"
python3 sms-cli.py list
python3 sms-cli.py list --status "REC UNREAD"
python3 sms-cli.py read 3
python3 sms-cli.py delete 3
python3 sms-cli.py delete-all
python3 sms-cli.py tokens
python3 sms-cli.py logs
python3 sms-cli.py logs --token admin --since 2026-05-01T00:00:00Z
python3 sms-cli.py logs --recipient +4512345678 --endpoint /sms/send
python3 sms-cli.py at AT+CSQ
python3 sms-cli.py ports

# Point at a non-default host
python3 sms-cli.py --host http://192.168.1.100:8000 status

# Raw JSON output (pipe to jq)
python3 sms-cli.py --json logs | jq '.entries[] | .recipient' | sort | uniq -c
```

---

## Finding the Right USB Port

The SIM7600E exposes several serial interfaces over USB. `ttyUSB2` is the AT command port on most systems, but you can confirm with:

```bash
for p in /dev/ttyUSB{0..7}; do
  echo -n "$p: "
  python3 -c "
import serial, time
try:
    s = serial.Serial('$p', 115200, timeout=1)
    s.write(b'AT\r\n'); time.sleep(0.3)
    print(repr(s.read(64)))
    s.close()
except Exception as e: print(e)"
done
```

The port responding with `b'\r\nOK\r\n'` is your AT command port. Set `MODEM_PORT` to that value in `.env`.

Alternatively, once the gateway is running, `GET /debug/ports` does the scan for you.

**Typical SIM7600E layout:**

| Port | Function |
|------|----------|
| `ttyUSB0` | Diagnostic |
| `ttyUSB1` | GPS / NMEA |
| `ttyUSB2` | **AT commands** ← default |
| `ttyUSB3` | PPP data modem |
| `ttyUSB4` | ADB |
| `ttyACM0` | CDC-NCM (network) |

---

## Home Assistant

Add a `rest_command` to send SMS from automations:

```yaml
# configuration.yaml
rest_command:
  send_sms:
    url: "http://192.168.1.100:8000/sms/send"
    method: POST
    headers:
      X-API-Key: "your-homeassistant-token"
      Content-Type: "application/json"
    payload: '{"to": "{{ number }}", "message": "{{ message }}"}'
```

```yaml
# Example automation action
service: rest_command.send_sms
data:
  number: "+4512345678"
  message: "Motion detected in garage!"
```

---

## Building from Source

```bash
git clone https://github.com/macsatcom/sms-gateway-sim7600.git
cd sms-gateway-sim7600

cp docker/.env.example docker/.env
# Edit docker/.env

docker compose -f docker/docker-compose.yml up --build
```

The Dockerfile build context is the repository root:
```bash
docker build -f docker/Dockerfile -t sms-gateway .
```

---

## Operations

```bash
docker compose up -d          # start in background
docker compose down           # stop and remove container
docker compose logs -f        # follow logs
docker compose pull           # pull latest image
```

Request logs and the SQLite database survive container restarts via the `sms_data` Docker volume.
