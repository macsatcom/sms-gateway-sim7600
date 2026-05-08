from __future__ import annotations

import os
import re
import threading
import time
from typing import Optional

import serial

# ── Configuration ─────────────────────────────────────────────────────────────
MODEM_PORT    = os.environ.get("MODEM_PORT",    "/dev/ttyUSB2")
MONITOR_PORT  = os.environ.get("MONITOR_PORT", "/dev/ttyUSB7")
BAUD_RATE     = int(os.environ.get("BAUD_RATE", "115200"))
CMD_TIMEOUT   = float(os.environ.get("CMD_TIMEOUT", "5.0"))
SIM_PIN       = os.environ.get("SIM_PIN", "")
DEBUG         = os.environ.get("DEBUG", "0").lower() in ("1", "true", "yes")
GPS_AUTOSTART = os.environ.get("GPS_AUTOSTART", "0").lower() in ("1", "true", "yes")


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
_CCID_RE    = re.compile(r'["\s]?(\d{18,22})["\s]?')
_CGPS_RE      = re.compile(r'\+CGPS:\s*(\d+),?(\d*)')
_CGPSINFO_RE  = re.compile(r'\+CGPSINFO:\s*([^,]*),([^,]*),([^,]*),([^,]*),([^,]*),([^,]*),([^,]*),([^,]*),([^,]*)')

# ── PDU helpers ───────────────────────────────────────────────────────────────

_GSM7_TABLE: dict[str, int] = {
    '@': 0,  '£': 1,  '$': 2,  '¥': 3,  'è': 4,  'é': 5,  'ù': 6,  'ì': 7,
    'ò': 8,  'Ç': 9,  '\n': 10, 'Ø': 11, 'ø': 12, '\r': 13, 'Å': 14, 'å': 15,
    'Δ': 16, '_': 17, 'Φ': 18, 'Γ': 19, 'Λ': 20, 'Ω': 21, 'Π': 22, 'Ψ': 23,
    'Σ': 24, 'Θ': 25, 'Ξ': 26, 'Æ': 28, 'æ': 29, 'ß': 30, 'É': 31,
    ' ': 32, '!': 33, '"': 34, '#': 35, '¤': 36, '%': 37, '&': 38, "'": 39,
    '(': 40, ')': 41, '*': 42, '+': 43, ',': 44, '-': 45, '.': 46, '/': 47,
    '0': 48, '1': 49, '2': 50, '3': 51, '4': 52, '5': 53, '6': 54, '7': 55,
    '8': 56, '9': 57, ':': 58, ';': 59, '<': 60, '=': 61, '>': 62, '?': 63,
    '¡': 64, 'A': 65, 'B': 66, 'C': 67, 'D': 68, 'E': 69, 'F': 70, 'G': 71,
    'H': 72, 'I': 73, 'J': 74, 'K': 75, 'L': 76, 'M': 77, 'N': 78, 'O': 79,
    'P': 80, 'Q': 81, 'R': 82, 'S': 83, 'T': 84, 'U': 85, 'V': 86, 'W': 87,
    'X': 88, 'Y': 89, 'Z': 90, 'Ä': 91, 'Ö': 92, 'Ñ': 93, 'Ü': 94, '§': 95,
    '¿': 96, 'a': 97, 'b': 98, 'c': 99, 'd': 100, 'e': 101, 'f': 102, 'g': 103,
    'h': 104, 'i': 105, 'j': 106, 'k': 107, 'l': 108, 'm': 109, 'n': 110, 'o': 111,
    'p': 112, 'q': 113, 'r': 114, 's': 115, 't': 116, 'u': 117, 'v': 118, 'w': 119,
    'x': 120, 'y': 121, 'z': 122, 'ä': 123, 'ö': 124, 'ñ': 125, 'ü': 126, 'à': 127,
}
_GSM7_CHARS = frozenset(_GSM7_TABLE)


def _pack_gsm7(text: str, fill_bits: int = 0) -> bytes:
    """Pack text into GSM 7-bit septets. fill_bits zero-bits precede the first septet."""
    out: list[int] = []
    bits = 0
    bit_count = fill_bits
    for c in text:
        bits |= (_GSM7_TABLE[c] & 0x7F) << bit_count
        bit_count += 7
        while bit_count >= 8:
            out.append(bits & 0xFF)
            bits >>= 8
            bit_count -= 8
    if bit_count > 0:
        out.append(bits & 0xFF)
    return bytes(out)


def _encode_phone_pdu(number: str) -> tuple[int, int, bytes]:
    """Encode a phone number as a PDU address field.
    Returns (digit_count, type_of_address, semi-octet bytes).
    """
    is_intl = number.startswith('+')
    digits  = ''.join(c for c in number if c.isdigit())
    toa     = 0x91 if is_intl else 0x81
    padded  = digits + ('F' if len(digits) % 2 else '')
    addr    = bytes(
        (int(padded[i + 1], 16) << 4) | int(padded[i], 16)
        for i in range(0, len(padded), 2)
    )
    return len(digits), toa, addr


def _build_sms_pdus(to: str, message: str) -> list[bytes]:
    """Build one or more SMS-SUBMIT PDUs, splitting long messages as needed.

    Uses GSM-7 encoding when all characters fit (includes Æ Ø Å æ ø å);
    falls back to UCS-2 for any character outside the GSM-7 alphabet.
    """
    use_ucs2   = not all(c in _GSM7_CHARS for c in message)
    max_single = 70  if use_ucs2 else 160
    max_seg    = 67  if use_ucs2 else 153

    segments = (
        [message] if len(message) <= max_single
        else [message[i:i + max_seg] for i in range(0, len(message), max_seg)]
    )
    total = len(segments)
    ref   = int(time.time()) & 0xFF

    digit_count, toa, addr_bytes = _encode_phone_pdu(to)
    dest = bytes([digit_count, toa]) + addr_bytes

    result: list[bytes] = []
    for seq, seg in enumerate(segments, 1):
        multi    = total > 1
        udhi_bit = 0x40 if multi else 0x00

        smsc  = b'\x00'             # use default SMSC from SIM
        first = bytes([0x11 | udhi_bit])  # SMS-SUBMIT, no VP
        mr    = b'\x00'
        pid   = b'\x00'

        if use_ucs2:
            dcs = b'\x08'
            if multi:
                udh = bytes([0x05, 0x00, 0x03, ref, total, seq])
                ud  = udh + seg.encode('utf-16-be')
            else:
                ud = seg.encode('utf-16-be')
            udl = bytes([len(ud)])      # UCS-2 UDL is in octets
        else:
            dcs = b'\x00'
            if multi:
                # 6-byte UDH occupies 7 septets; 1 fill bit before packed message
                udh = bytes([0x05, 0x00, 0x03, ref, total, seq])
                ud  = udh + _pack_gsm7(seg, fill_bits=1)
                udl = bytes([7 + len(seg)])  # 7 header septets + message chars
            else:
                ud  = _pack_gsm7(seg)
                udl = bytes([len(seg)])      # GSM-7 UDL is in septets (chars)

        result.append(smsc + first + mr + dest + pid + dcs + udl + ud)

    return result


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
        self._gps_running: Optional[bool] = None  # None = not yet queried

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

        # UCS-2 charset: message text is sent/received as UTF-16 BE hex strings.
        # Handles all Unicode including Danish ÆØÅæøå, emoji, etc.
        resp = self._send_command('AT+CSCS="UCS2"')
        if "OK" not in resp:
            raise ModemError(f'AT+CSCS="UCS2" failed: {resp!r}')

        # Select SIM storage
        resp = self._send_command('AT+CPMS="SM","SM","SM"')
        if "OK" not in resp:
            raise ModemError(f"AT+CPMS failed: {resp!r}")

        if GPS_AUTOSTART:
            try:
                self.gps_start()
                print("[modem] GPS started (GPS_AUTOSTART=1)", flush=True)
            except ModemError as e:
                print(f"[modem] WARNING: GPS autostart failed: {e}", flush=True)

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
                    decoded = buf.decode("latin-1")
                    if wait_for in decoded or "ERROR" in decoded:
                        break

            response = buf.decode("latin-1")
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

            # Step 1: send destination number as UCS-2 hex, wait for > prompt
            # AT+CSCS="UCS2" requires the number string to be UTF-16 BE hex too.
            to_hex = to.encode("utf-16-be").hex().upper()
            _log(f"[modem] >> AT+CMGS=\"{to_hex}\" (UCS-2 of {to!r})")
            self._serial.write(f'AT+CMGS="{to_hex}"\r'.encode())

            prompt_buf = b""
            deadline = time.time() + 5.0
            while time.time() < deadline:
                chunk = self._serial.read(max(self._serial.in_waiting, 1))
                if chunk:
                    prompt_buf += chunk
                    if b">" in prompt_buf:
                        break

            if b">" not in prompt_buf:
                raise ModemError(f"No > prompt from modem (got: {prompt_buf!r})")
            _log(f"[modem] << {prompt_buf!r}")

            # Step 2: send message as UTF-16 BE hex + Ctrl+Z
            # AT+CSCS="UCS2" is set at init — modem expects the body as a hex
            # string of UCS-2 (UTF-16 BE) encoded characters.
            body_hex = message.encode("utf-16-be").hex().upper().encode()
            _log(f"[modem] >> <UCS2 hex, {len(message)} chars><CTRL+Z>")
            self._serial.write(body_hex + b"\x1a")

            # Step 3: wait for +CMGS confirmation (network can be slow)
            deadline = time.time() + 30.0
            buf = b""
            while time.time() < deadline:
                chunk = self._serial.read(max(self._serial.in_waiting, 1))
                if chunk:
                    buf += chunk
                    decoded = buf.decode("latin-1")
                    if "+CMGS:" in decoded or "ERROR" in decoded:
                        break

            response = buf.decode("latin-1")
            _log(f"[modem] << (send result) {response!r}")

            if "ERROR" in response and "+CMGS:" not in response:
                raise ModemError(response.strip())

            m = _CMGS_RE.search(response)
            return int(m.group(1)) if m else 0

    @staticmethod
    def _decode_body(raw: str) -> str:
        """Decode a UCS-2 hex body line to a Python string.
        Falls back to returning the raw string if it is not valid hex."""
        s = raw.strip()
        if len(s) >= 4 and len(s) % 4 == 0 and all(c in "0123456789ABCDEFabcdef" for c in s):
            try:
                return bytes.fromhex(s).decode("utf-16-be")
            except (ValueError, UnicodeDecodeError):
                pass
        return s

    def list_sms(self, status_filter: str = "ALL") -> list[dict]:
        resp = self._send_command('AT+CMGL="ALL"', extra_timeout=5.0)

        messages = []
        current: Optional[dict] = None
        body_lines: list[str] = []

        def _flush():
            if current is not None:
                raw = "\n".join(body_lines).strip()
                current["message"] = self._decode_body(raw)
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
            "message":   self._decode_body("\n".join(body_lines).strip()),
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

    # ── GPS methods ───────────────────────────────────────────────────────────

    def gps_start(self) -> dict:
        status = self.get_gps_status()
        if status["running"]:
            return status  # already on — idempotent
        resp = self._send_command("AT+CGPS=1,1")
        if "OK" not in resp:
            raise ModemError(f"AT+CGPS=1,1 failed: {resp.strip()!r}")
        self._gps_running = True
        return {"running": True, "streaming": True}

    def gps_stop(self) -> dict:
        status = self.get_gps_status()
        if not status["running"]:
            return status  # already off — idempotent
        resp = self._send_command("AT+CGPS=0")
        if "OK" not in resp:
            raise ModemError(f"AT+CGPS=0 failed: {resp.strip()!r}")
        self._gps_running = False
        return {"running": False, "streaming": False}

    def get_gps_status(self) -> dict:
        resp = self._send_command("AT+CGPS?")
        m = _CGPS_RE.search(resp)
        running   = bool(int(m.group(1))) if m else False
        streaming = bool(int(m.group(2))) if (m and m.group(2)) else False
        self._gps_running = running  # keep cached state in sync
        return {
            "running":   running,
            "streaming": streaming,
        }

    def get_gps_location(self) -> dict:
        # Use cached state to avoid a round-trip that may lag behind start/stop
        if self._gps_running is None:
            self._gps_running = self.get_gps_status()["running"]
        if not self._gps_running:
            raise ModemError("GPS not running — call POST /gps/start first")

        resp = self._send_command("AT+CGPSINFO", extra_timeout=2.0)
        m = _CGPSINFO_RE.search(resp)
        if not m:
            raise ModemError(f"Unexpected CGPSINFO response: {resp.strip()!r}")

        # +CGPSINFO: lat,N/S,lon,E/W,date(DDMMYY),time(HHMMSS.sss),alt,speed,course
        lat_raw, ns, lon_raw, ew, date_raw, time_raw, alt_raw, spd_raw, crs_raw = (
            g.strip() for g in m.groups()
        )

        if not lat_raw:
            raise ModemError("No GPS fix yet — antenna needs clear sky view")

        def _nmea_to_dd(nmea: str, hemi: str, lon: bool) -> float:
            """Convert NMEA DDDMM.MMMM to decimal degrees."""
            deg_digits = 3 if lon else 2
            degrees = float(nmea[:deg_digits])
            minutes = float(nmea[deg_digits:])
            dd = degrees + minutes / 60.0
            return -dd if hemi in ("S", "W") else dd

        def _opt_float(s: str) -> Optional[float]:
            try:
                return float(s) if s else None
            except ValueError:
                return None

        # Parse date+time: DDMMYY + HHMMSS.sss → ISO-8601
        utc_iso: Optional[str] = None
        if len(date_raw) >= 6 and len(time_raw) >= 6:
            utc_iso = (
                f"20{date_raw[4:6]}-{date_raw[2:4]}-{date_raw[0:2]}"
                f"T{time_raw[0:2]}:{time_raw[2:4]}:{time_raw[4:6]}Z"
            )

        return {
            "fix":               True,
            "latitude":          _nmea_to_dd(lat_raw, ns, lon=False),
            "longitude":         _nmea_to_dd(lon_raw, ew, lon=True),
            "altitude_m":        _opt_float(alt_raw),
            "speed_kmh":         _opt_float(spd_raw),
            "course_deg":        _opt_float(crs_raw),
            "hdop":              None,
            "satellites_in_view": None,
            "utc_datetime":      utc_iso,
        }

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
