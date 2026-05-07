# SMS Gateway — SIM7600E
# FastAPI + pyserial REST API for AT-command SMS operations.
#
# Build context must be the repository root (SMS_Gateway_SIM7600/):
#   docker build -f docker/Dockerfile -t sms-gateway .
#
# Normally use docker-compose instead — see docker/docker-compose.yml.

FROM python:3.12-slim

# ── System dependencies ───────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ───────────────────────────────────────────────────────
RUN pip install --no-cache-dir \
        "fastapi==0.111.*" \
        "uvicorn[standard]==0.30.*" \
        "pyserial==3.5"

# ── Application ───────────────────────────────────────────────────────────────
COPY app/ /app/

EXPOSE 8000 8007

CMD ["python3", "/app/server.py"]
