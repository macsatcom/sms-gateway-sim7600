from __future__ import annotations

import os
import re
import threading
import time
from typing import Optional

import serial

# ── Configuration ─────────────────────────────────────────────────────────────
MODEM_PORT  = os.environ.get("MODEM_PORT",  "/dev/ttyUSB2")
BAUD_RATE   = int(os.environ.get("BAUD_RATE", "115200"))
CMD_TIMEOUT = float(os.environ.get("CMD_TIMEOUT", "5.0"))
SIM_PIN     = os.environ.get("SIM_PIN", "")
DEBUG       = os.environ.get("DEBUG", "0").lower() in ("1", "true", "yes")


def _log(msg: str) -> None:
    if DEBUG:
        print(msg, flush=True)

# ── AT response patterns ───────────────────────────────────────────────────────
_CPIN_RE = re.compile(r'\+CPIN:\s*(\S+)')
_CSCA_RE = re.compile(r'\+CSCA:\s*"([^"]+)"')
_CMGL_RE = re.compile(r'\+CMGL:\s*(\d+),"([^"]+)","([^"]*)",[^,]*,"([^"]+)"')
_CMGR_RE = re.compile(r'\+CMGR:\s*"([^"]+)","([^"]*)",[^,]*,"([^"]+)"')
_CSQ_RE  = re.compile(r'\+CSQ:\s*(\d+),')
_CREG_RE = re.compile(r'\+CREG:\s*\d+,(\d+)')
_CREG_SIMPLE_RE = re.compile(r'\+CREG:\s*(\d+)')
_CMGS_RE = re.compile(r'\+CMGS:\s*(\d+)')
_CCID_RE = re.compile(r'["\s]?(\d{18,22})["\s]?')

_CREG_TEXT = {
    "0": "Not registered, not searching",
    "1": "Registered, home network",
    "2": "Not registered, searching",
    "3": "Registration denied",
    "4": "Unknown",
    "5": "Registered, roaming",
}


class ModemError(Exception):
    """Raised when the modem returns ERROR or is unreachable."""
    pass


class ModemManager:
    def __init__(self, port: str, baud: int, timeout: float):
        self._port    = port
        self._baud    = baud
        self._timeout = timeout
        self._serial: Optional[serial.Serial] = None
        self._lock    = threading.Lock()

    def open(self) -> None:
        # dsrdtr/rtscts=False prevents pyserial from toggling DTR/RTS on open,
        # which can cause the SIM7600E to exit AT command mode.
        self._serial = serial.Serial(
            self._port, self._baud,
            timeout=0.1, dsrdtr=False, rtscts=False,
        )
        # Give the modem time to settle after (re)connecting
        time.sleep(1.0)

        # Cancel any partial command left over from a previous session, then flush
        self._serial.write(b"\r\n")
        time.sleep(0.3)
        self._serial.flushInput()

        # Some modems need a couple of wakeup attempts after a cold open
        for attempt in range(1, 4):
            resp = self._send_command("AT")
            if "OK" in resp:
                break
            print(f"[modem] AT attempt {attempt}/3: no OK (got {resp!r}), retrying...", flush=True)
            time.sleep(1.0)
        else:
            self._serial.close()
            raise ModemError(f"Modem on {self._port} did not respond to AT after 3 attempts")

        # Check PIN status and unlock if needed
        pin_state = self.get_pin_state()
        if pin_state == "SIM PIN":
            if SIM_PIN:
                self.enter_pin(SIM_PIN)
            else:
                raise ModemError("SIM requires a PIN but SIM_PIN env var is not set")
        elif pin_state == "SIM PUK":
            raise ModemError("SIM is PUK-locked — manual intervention required")
        # "READY" means no PIN needed, proceed normally

        # Set text mode
        resp = self._send_command("AT+CMGF=1")
        if "OK" not in resp:
            raise ModemError(f"AT+CMGF=1 failed: {resp!r}")

        # Select SIM storage
        resp = self._send_command('AT+CPMS="SM","SM","SM"')
        if "OK" not in resp:
            raise ModemError(f"AT+CPMS failed: {resp!r}")

    def get_pin_state(self) -> str:
        """Return the raw CPIN state: 'READY', 'SIM PIN', 'SIM PUK', etc."""
        resp = self._send_command("AT+CPIN?")
        m = _CPIN_RE.search(resp)
        return m.group(1) if m else "UNKNOWN"

    def enter_pin(self, pin: str) -> None:
        """Send PIN to unlock the SIM. Raises ModemError on wrong PIN."""
        resp = self._send_command(f"AT+CPIN={pin}")
        if "OK" not in resp:
            raise ModemError(f"PIN entry failed: {resp.strip()!r}")
        time.sleep(1)  # give modem time to register on network after unlock

    def _send_command(self, cmd: str, wait_for: str = "OK",
                      extra_timeout: float = 0.0) -> str:
        with self._lock:
            if not self._serial or not self._serial.is_open:
                raise ModemError("Serial port not open")

            _log(f"[modem] >> {cmd!r}")
            self._serial.flushInput()
            self._serial.write((cmd + "\r\n").encode())

            deadline = time.time() + self._timeout + extra_timeout
            buf = b""
            while time.time() < deadline:
                # read() blocks for at most serial.timeout (0.1s) then returns b""
                chunk = self._serial.read(max(self._serial.in_waiting, 1))
                if chunk:
                    buf += chunk
                    decoded = buf.decode("utf-8", errors="replace")
                    if wait_for in decoded or "ERROR" in decoded:
                        break

            response = buf.decode("utf-8", errors="replace")
            _log(f"[modem] << {response!r}")

            # Strip echo: first non-empty line often mirrors the command
            lines = response.splitlines()
            filtered = [l for l in lines if l.strip() and l.strip() != cmd.strip()]
            clean = "\n".join(filtered)

            if "ERROR" in clean and wait_for not in clean:
                raise ModemError(clean.strip())

            return response

    def check_health(self) -> dict:
        try:
            resp = self._send_command("AT")
            if "OK" in resp:
                return {"status": "ok", "port": self._port, "message": "Modem responsive"}
            return {"status": "error", "port": self._port, "message": f"Unexpected response: {resp!r}"}
        except Exception as e:
            return {"status": "error", "port": self._port, "message": str(e)}

    def get_status(self) -> dict:
        def _cmd(cmd):
            try:
                return self._send_command(cmd)
            except ModemError:
                return ""

        imsi_resp = _cmd("AT+CIMI")
        ccid_resp = _cmd("AT+CCID")
        csq_resp  = _cmd("AT+CSQ")
        creg_resp = _cmd("AT+CREG?")
        csca_resp = _cmd("AT+CSCA?")

        # Parse IMSI — digits-only line
        imsi = None
        for line in imsi_resp.splitlines():
            line = line.strip()
            if line.isdigit() and len(line) >= 14:
                imsi = line
                break

        # Parse CCID
        ccid = None
        m = _CCID_RE.search(ccid_resp)
        if m:
            ccid = m.group(1)

        # Parse CSQ
        signal_strength = None
        signal_dbm = None
        m = _CSQ_RE.search(csq_resp)
        if m:
            rssi = int(m.group(1))
            signal_strength = rssi
            if rssi != 99:
                signal_dbm = -113.0 + 2 * rssi

        # Parse CREG
        network_registration = None
        m = _CREG_RE.search(creg_resp)
        if m:
            network_registration = m.group(1)
        else:
            m = _CREG_SIMPLE_RE.search(creg_resp)
            if m:
                network_registration = m.group(1)

        # Parse SMSC
        smsc = None
        m = _CSCA_RE.search(csca_resp)
        if m:
            smsc = m.group(1)

        return {
            "imsi": imsi,
            "ccid": ccid,
            "signal_strength": signal_strength,
            "signal_dbm": signal_dbm,
            "network_registration": network_registration,
            "network_registration_text": _CREG_TEXT.get(
                network_registration or "", "Unknown"
            ),
            "smsc": smsc,
            "modem_port": self._port,
        }

    def send_sms(self, to: str, message: str) -> int:
        with self._lock:
            if not self._serial or not self._serial.is_open:
                raise ModemError("Serial port not open")

            self._serial.flushInput()

            # Step 1: send phone number, wait for > prompt
            _log(f"[modem] >> AT+CMGS=\"{to}\"")
            self._serial.write(f'AT+CMGS="{to}"\r'.encode())

            prompt_buf = b""
            deadline = time.time() + 5.0
            while time.time() < deadline:
                # read() returns after 0.1s if nothing arrives (serial timeout)
                chunk = self._serial.read(max(self._serial.in_waiting, 1))
                if chunk:
                    prompt_buf += chunk
                    if b">" in prompt_buf:
                        break

            if b">" not in prompt_buf:
                raise ModemError(
                    f"No > prompt from modem (got: {prompt_buf!r})"
                )
            _log(f"[modem] << {prompt_buf!r}")

            # Step 2: send message body + Ctrl+Z
            self._serial.write(message.encode("utf-8", errors="replace") + b"\x1a")

            # Step 3: wait for +CMGS confirmation (long — network can be slow)
            deadline = time.time() + 30.0
            buf = b""
            while time.time() < deadline:
                chunk = self._serial.read(max(self._serial.in_waiting, 1))
                if chunk:
                    buf += chunk
                    decoded = buf.decode("utf-8", errors="replace")
                    if "+CMGS:" in decoded or "ERROR" in decoded:
                        break

            response = buf.decode("utf-8", errors="replace")
            _log(f"[modem] << (send result) {response!r}")

            if "ERROR" in response and "+CMGS:" not in response:
                raise ModemError(response.strip())

            m = _CMGS_RE.search(response)
            return int(m.group(1)) if m else 0

    def list_sms(self, status_filter: str = "ALL") -> list[dict]:
        resp = self._send_command('AT+CMGL="ALL"', extra_timeout=5.0)

        messages = []
        current: Optional[dict] = None
        body_lines: list[str] = []

        def _flush():
            if current is not None:
                current["message"] = "\n".join(body_lines).strip()
                messages.append(current)

        for line in resp.splitlines():
            line_stripped = line.strip()
            m = _CMGL_RE.match(line_stripped)
            if m:
                _flush()
                current = {
                    "index":     int(m.group(1)),
                    "status":    m.group(2),
                    "sender":    m.group(3),
                    "timestamp": m.group(4),
                    "message":   "",
                }
                body_lines = []
            elif current is not None and line_stripped and line_stripped != "OK":
                body_lines.append(line_stripped)

        _flush()

        if status_filter != "ALL":
            messages = [msg for msg in messages if msg["status"] == status_filter]

        return messages

    def read_sms(self, index: int) -> dict:
        resp = self._send_command(f"AT+CMGR={index}")

        m = _CMGR_RE.search(resp)
        if not m:
            raise ModemError(f"SMS index {index} not found")

        lines = resp.splitlines()
        body_lines = []
        past_header = False
        for line in lines:
            if "+CMGR:" in line:
                past_header = True
                continue
            if past_header and line.strip() and line.strip() != "OK":
                body_lines.append(line.strip())

        return {
            "index":     index,
            "status":    m.group(1),
            "sender":    m.group(2),
            "timestamp": m.group(3),
            "message":   "\n".join(body_lines).strip(),
        }

    def delete_sms(self, index: int) -> None:
        resp = self._send_command(f"AT+CMGD={index}")
        if "OK" not in resp:
            raise ModemError(f"Delete failed: {resp.strip()!r}")

    def delete_all_sms(self) -> None:
        # delflag=4 means delete all messages from all storages
        resp = self._send_command("AT+CMGD=1,4", extra_timeout=10.0)
        if "OK" not in resp:
            raise ModemError(f"Delete all failed: {resp.strip()!r}")

    @staticmethod
    def scan_ports(baud: int) -> list[dict]:
        """Probe /dev/ttyUSB0–7 and report which respond to AT."""
        results = []
        for i in range(8):
            port = f"/dev/ttyUSB{i}"
            try:
                s = serial.Serial(port, baud, timeout=1, dsrdtr=False, rtscts=False)
                s.write(b"AT\r\n")
                time.sleep(0.3)
                raw = s.read(64).decode("utf-8", errors="replace")
                s.close()
                ok = "OK" in raw
                results.append({
                    "port": port,
                    "status": "responsive" if ok else "no_response",
                    "response": raw.strip(),
                })
            except Exception as e:
                results.append({
                    "port": port,
                    "status": "error",
                    "error": str(e),
                })
        return results


if __name__ == "__main__":
    import json

    m = ModemManager(MODEM_PORT, BAUD_RATE, CMD_TIMEOUT)
    print(f"Opening {MODEM_PORT} at {BAUD_RATE} baud...")
    m.open()
    print("Health:", json.dumps(m.check_health(), indent=2))
    print("Status:", json.dumps(m.get_status(), indent=2))
    print("SMS list:", json.dumps(m.list_sms(), indent=2))
