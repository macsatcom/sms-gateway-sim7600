from __future__ import annotations

import asyncio
import re
import threading
import time
from typing import Callable, Optional

import serial

_CMTI_RE = re.compile(r'\+CMTI:\s*"[^"]+",(\d+)')


class SmsMonitor:
    """Listens on a secondary AT port for +CMTI URCs and broadcasts incoming SMS to SSE subscribers."""

    def __init__(self, port: str, baud: int):
        self._port = port
        self._baud = baud
        self._subscribers: list[asyncio.Queue] = []
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def _broadcast(self, event: dict) -> None:
        with self._lock:
            subs = list(self._subscribers)
        if self._loop:
            for q in subs:
                self._loop.call_soon_threadsafe(q.put_nowait, event)

    def start(self, loop: asyncio.AbstractEventLoop, read_sms: Callable[[int], dict]) -> None:
        self._loop = loop
        t = threading.Thread(target=self._run, args=(read_sms,), daemon=True)
        t.start()
        print(f"[monitor] Listening for incoming SMS on {self._port}", flush=True)

    def _run(self, read_sms: Callable[[int], dict]) -> None:
        while True:
            s = None
            try:
                s = serial.Serial(self._port, self._baud, timeout=1, dsrdtr=False, rtscts=False)
                time.sleep(0.5)
                s.flushInput()

                # Wake up port and verify it responds to AT commands
                s.write(b"AT\r\n")
                time.sleep(0.3)
                wake_resp = s.read(64).decode("latin-1")
                if "OK" not in wake_resp:
                    raise RuntimeError(f"port did not respond to AT (got {wake_resp!r})")

                s.write(b"AT+CNMI=2,1,0,0,0\r\n")
                time.sleep(0.3)
                cnmi_resp = s.read(64).decode("latin-1")
                if "OK" not in cnmi_resp:
                    raise RuntimeError(f"AT+CNMI setup failed (got {cnmi_resp!r})")
                print(f"[monitor] +CNMI configured on {self._port}", flush=True)

                buf = ""
                while True:
                    raw = s.read(max(s.in_waiting, 1))
                    if raw:
                        buf += raw.decode("latin-1")
                        while "\n" in buf:
                            line, buf = buf.split("\n", 1)
                            m = _CMTI_RE.search(line)
                            if m:
                                index = int(m.group(1))
                                print(f"[monitor] +CMTI index={index}", flush=True)
                                try:
                                    sms = read_sms(index)
                                    self._broadcast(sms)
                                except Exception as e:
                                    print(f"[monitor] read_sms({index}) failed: {e}", flush=True)
            except Exception as e:
                print(f"[monitor] {self._port} error: {e} — retrying in 5s", flush=True)
                if s:
                    try:
                        s.close()
                    except Exception:
                        pass
                time.sleep(5)
