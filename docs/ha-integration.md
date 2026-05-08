# Home Assistant Integration

The SMS gateway exposes a **Server-Sent Events (SSE)** endpoint that streams incoming SMS messages as they arrive. Home Assistant connects outbound to the gateway — no ports need to be open on the HA side.

## How it works

```
Phone → SMS → SIM7600E (ttyUSB7 URC) → gateway /sms/stream → HA consumer → HA event bus
```

1. The gateway monitors `ttyUSB7` for `+CMTI` notifications (new SMS stored)
2. It reads the message and pushes it to all connected SSE clients
3. A consumer script running on the HA host subscribes to the stream and fires HA events

## SSE Endpoint

```
GET http://<gateway-ip>:8000/sms/stream
X-API-Key: <your-token>
```

**Response** — `text/event-stream`, one event per incoming SMS:

```
data: {"index": 3, "sender": "+4512345678", "message": "Hello!", "timestamp": "26/05/08,10:30:00+08", "status": "REC UNREAD"}

: keepalive
```

Test it with curl:

```bash
curl -N -H "X-API-Key: $KEY" http://localhost:8000/sms/stream
```

## Consumer script for Home Assistant

Save this as `/config/sms_consumer.py` (or anywhere accessible on the HA host).

```python
#!/usr/bin/env python3
"""
Subscribes to the SMS gateway SSE stream and fires a Home Assistant event
for each incoming SMS. Run alongside HA as a long-lived process.

Event fired: sms_received
Event data:  {"sender": "+45...", "message": "...", "timestamp": "..."}
"""
import json
import time
import urllib.request

GATEWAY_URL  = "http://192.168.1.x:8000/sms/stream"   # adjust
GATEWAY_KEY  = "your-api-key"
HA_URL       = "http://localhost:8123"
HA_TOKEN     = "your-ha-long-lived-access-token"


def fire_ha_event(data: dict) -> None:
    payload = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{HA_URL}/api/events/sms_received",
        data=payload,
        headers={
            "Authorization": f"Bearer {HA_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        print(f"[ha] event fired, status={resp.status}", flush=True)


def stream() -> None:
    req = urllib.request.Request(
        GATEWAY_URL,
        headers={"X-API-Key": GATEWAY_KEY},
    )
    with urllib.request.urlopen(req) as resp:
        buf = ""
        while True:
            chunk = resp.read(1).decode("utf-8")
            if not chunk:
                break
            buf += chunk
            if buf.endswith("\n\n"):
                for line in buf.strip().splitlines():
                    if line.startswith("data:"):
                        payload = json.loads(line[5:].strip())
                        print(f"[sms] from={payload.get('sender')} msg={payload.get('message')!r}", flush=True)
                        fire_ha_event({
                            "sender":    payload.get("sender"),
                            "message":   payload.get("message"),
                            "timestamp": payload.get("timestamp"),
                        })
                buf = ""


while True:
    try:
        print("[sms-consumer] connecting...", flush=True)
        stream()
    except Exception as e:
        print(f"[sms-consumer] error: {e} — retrying in 10s", flush=True)
        time.sleep(10)
```

## Home Assistant automation

Once the consumer is running, use the `sms_received` event as an automation trigger:

```yaml
automation:
  - alias: "Handle incoming SMS"
    trigger:
      - platform: event
        event_type: sms_received
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "SMS from {{ trigger.event.data.sender }}"
          message: "{{ trigger.event.data.message }}"
```

## Running the consumer as a service

### systemd (on the HA host)

```ini
# /etc/systemd/system/sms-consumer.service
[Unit]
Description=SMS Gateway consumer for Home Assistant
After=network.target

[Service]
ExecStart=/usr/bin/python3 /config/sms_consumer.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now sms-consumer
sudo journalctl -u sms-consumer -f
```

### Docker Compose (alongside the gateway)

Add to `docker/docker-compose.yml`:

```yaml
  sms-consumer:
    image: python:3.12-slim
    volumes:
      - ./sms_consumer.py:/sms_consumer.py
    environment:
      - GATEWAY_URL=http://sms-gateway:8000/sms/stream
      - GATEWAY_KEY=your-token
      - HA_URL=http://192.168.1.x:8123
      - HA_TOKEN=your-ha-token
    command: python3 /sms_consumer.py
    restart: unless-stopped
    depends_on:
      - sms-gateway
```

## Getting a Home Assistant long-lived token

In HA: **Profile → Security → Long-lived access tokens → Create token**
