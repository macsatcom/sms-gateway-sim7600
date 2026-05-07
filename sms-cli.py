#!/usr/bin/env python3
"""
sms-cli  —  command-line client for the SIM7600E SMS Gateway API

Token lookup order (first match wins):
  1. --key / -k flag
  2. SMS_GATEWAY_KEY environment variable
  3. .token file in the same directory as this script

No third-party dependencies — uses only the Python standard library.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

# ── Defaults ───────────────────────────────────────────────────────────────────
SCRIPT_DIR         = Path(__file__).resolve().parent
DEFAULT_TOKEN_FILE = SCRIPT_DIR / ".token"
DEFAULT_HOST       = "http://localhost:8000"
VERSION            = "1.0.0"


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _request(
    host: str,
    token: str,
    method: str,
    path: str,
    params: Optional[dict] = None,
    body: Optional[dict] = None,
) -> Any:
    url = host.rstrip("/") + path
    if params:
        filtered = {k: v for k, v in params.items() if v is not None}
        if filtered:
            url += "?" + urllib.parse.urlencode(filtered)

    data = json.dumps(body).encode("utf-8") if body is not None else None
    req  = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-API-Key", token)

    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body_bytes = e.read()
        try:
            err    = json.loads(body_bytes)
            detail = err.get("detail") or err.get("error") or json.dumps(err)
        except Exception:
            detail = body_bytes.decode(errors="replace")
        _die(f"HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        _die(f"Connection error: {e.reason}\nIs the gateway running at {host}?")


def _die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def _out(data: Any, raw: bool) -> None:
    print(json.dumps(data) if raw else json.dumps(data, indent=2))


# ── Human-readable formatters ──────────────────────────────────────────────────

def _fmt_health(d: dict) -> str:
    return "\n".join([
        f"Status:   {d['status']}",
        f"Message:  {d['message']}",
        f"Port:     {d['port']}",
    ])


def _fmt_status(d: dict) -> str:
    sig = (f"{d['signal_dbm']} dBm  (rssi={d['signal_strength']})"
           if d.get("signal_dbm") is not None else "Unknown")
    return "\n".join([
        f"IMSI:          {d.get('imsi') or '—'}",
        f"CCID:          {d.get('ccid') or '—'}",
        f"Signal:        {sig}",
        f"Registration:  {d.get('network_registration_text', '—')}",
        f"SMSC:          {d.get('smsc') or '—'}",
        f"Port:          {d.get('modem_port', '—')}",
    ])


def _fmt_sms_list(d: dict) -> str:
    messages = d.get("messages", [])
    count    = d.get("count", 0)
    if not messages:
        return f"No messages stored (total: {count})"
    idx_w = max(len(str(m["index"])) for m in messages)
    lines = [f"Stored messages: {count}\n"]
    for m in messages:
        body = m["message"].replace("\n", " ")
        if len(body) > 55:
            body = body[:52] + "..."
        lines.append(
            f"  #{str(m['index']).rjust(idx_w)}  "
            f"{m['status']:<12}  "
            f"{m['sender']:<16}  "
            f"{m['timestamp']}  "
            f"{body}"
        )
    return "\n".join(lines)


def _fmt_sms_message(d: dict) -> str:
    return "\n".join([
        f"Index:     {d['index']}",
        f"Status:    {d['status']}",
        f"From:      {d['sender']}",
        f"Timestamp: {d['timestamp']}",
        f"",
        d["message"],
    ])


def _fmt_tokens(d: dict) -> str:
    tokens = d.get("tokens", [])
    auth   = "enabled" if d.get("auth_enabled") else "disabled (no tokens configured)"
    if not tokens:
        return f"Auth: {auth}\nNo tokens found in log history."
    name_w = max(len(t["name"]) for t in tokens)
    sep    = "─" * (name_w + 2 + 10 + 2 + 10 + 2 + 24)
    header = f"  {'Name':<{name_w}}  {'Requests':>10}  {'SMS Sent':>10}  Last Used"
    rows   = [f"Auth: {auth}\n", header, sep]
    for t in tokens:
        rows.append(
            f"  {t['name']:<{name_w}}  "
            f"{t['request_count']:>10}  "
            f"{t['sms_sent']:>10}  "
            f"{t.get('last_used') or '—'}"
        )
    return "\n".join(rows)


def _fmt_logs(d: dict) -> str:
    entries = d.get("entries", [])
    total   = d.get("total", 0)
    limit   = d.get("limit", len(entries))
    offset  = d.get("offset", 0)

    if not entries:
        return f"No log entries match the given filters (total matching: {total})"

    name_w = max(len(e["token_name"]) for e in entries)
    ep_w   = max(len(e["endpoint"])   for e in entries)
    sep    = "─" * (20 + 2 + name_w + 2 + 6 + 2 + ep_w + 2 + 4 + 2 + 18)
    header = (
        f"  {'Timestamp':<20}  {'Token':<{name_w}}  "
        f"{'Method':<6}  {'Endpoint':<{ep_w}}  {'Code'}  Recipient"
    )
    rows = [header, sep]
    for e in entries:
        rows.append(
            f"  {e['timestamp']:<20}  {e['token_name']:<{name_w}}  "
            f"{e['method']:<6}  {e['endpoint']:<{ep_w}}  "
            f"{e['status_code']:<4}  {e.get('recipient') or '—'}"
        )
    rows.append(f"\n  Showing {len(entries)} of {total} total  "
                f"(offset={offset}, limit={limit})")
    return "\n".join(rows)


def _fmt_ports(d: dict) -> str:
    rows = ["  Port              Status          Response / Error"]
    rows.append("  " + "─" * 70)
    for p in d.get("ports", []):
        detail = p.get("response") or p.get("error") or ""
        if detail:
            detail = detail[:60]
        rows.append(f"  {p['port']:<18}  {p['status']:<14}  {detail}")
    return "\n".join(rows)


# ── Command handlers ───────────────────────────────────────────────────────────

def cmd_health(args, token):
    r = _request(args.host, "", "GET", "/health")
    _out(r, args.json) if args.json else print(_fmt_health(r))


def cmd_status(args, token):
    r = _request(args.host, token, "GET", "/status")
    _out(r, args.json) if args.json else print(_fmt_status(r))


def cmd_send(args, token):
    r = _request(args.host, token, "POST", "/sms/send",
                 body={"to": args.to, "message": args.message})
    if args.json:
        _out(r, True)
    else:
        ref = r.get("message_reference")
        print(f"Sent  ok={r['ok']}  message_reference={ref}")


def cmd_list(args, token):
    params = {}
    if args.status:
        params["status"] = args.status
    r = _request(args.host, token, "GET", "/sms", params=params)
    _out(r, args.json) if args.json else print(_fmt_sms_list(r))


def cmd_read(args, token):
    r = _request(args.host, token, "GET", f"/sms/{args.index}")
    _out(r, args.json) if args.json else print(_fmt_sms_message(r))


def cmd_delete(args, token):
    r = _request(args.host, token, "DELETE", f"/sms/{args.index}")
    _out(r, args.json) if args.json else print(f"Deleted index {args.index}.")


def cmd_delete_all(args, token):
    if not args.yes:
        try:
            ans = input("Delete ALL stored messages? This cannot be undone. [y/N] ")
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            sys.exit(0)
        if ans.strip().lower() != "y":
            print("Aborted.")
            sys.exit(0)
    r = _request(args.host, token, "DELETE", "/sms")
    _out(r, args.json) if args.json else print("All messages deleted.")


def cmd_pin(args, token):
    r = _request(args.host, token, "GET", "/sim/pin")
    if args.json:
        _out(r, True)
    else:
        print(f"State:       {r['state']}")
        print(f"Description: {r['description']}")


def cmd_unlock(args, token):
    r = _request(args.host, token, "POST", "/sim/pin", body={"pin": args.pin})
    if args.json:
        _out(r, True)
    else:
        print(f"State:       {r['state']}")
        print(f"Description: {r['description']}")


def cmd_tokens(args, token):
    r = _request(args.host, token, "GET", "/tokens")
    _out(r, args.json) if args.json else print(_fmt_tokens(r))


def cmd_logs(args, token):
    params = {
        "limit":     args.limit,
        "offset":    args.offset,
        "token":     args.token_name,
        "since":     args.since,
        "until":     args.until,
        "recipient": args.recipient,
        "endpoint":  args.endpoint,
    }
    r = _request(args.host, token, "GET", "/logs", params=params)
    _out(r, args.json) if args.json else print(_fmt_logs(r))


def cmd_at(args, token):
    r = _request(args.host, token, "GET", "/debug/at", params={"cmd": args.cmd})
    if args.json:
        _out(r, True)
    else:
        marker = "OK   " if r["ok"] else "ERROR"
        print(f"[{marker}] {r['response'].strip()}")


def cmd_ports(args, token):
    r = _request(args.host, token, "GET", "/debug/ports")
    _out(r, args.json) if args.json else print(_fmt_ports(r))


# ── Argument parser ────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sms-cli",
        description=textwrap.dedent("""\
            SMS Gateway CLI — command-line client for the SIM7600E REST API.

            Token lookup order (first match wins):
              1. --key / -k flag
              2. SMS_GATEWAY_KEY environment variable
              3. .token file in the same directory as this script
                 (create with: echo 'your-api-key' > .token)
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Quick examples:
              %(prog)s health
              %(prog)s status
              %(prog)s send +4512345678 "Hello from CLI"
              %(prog)s list
              %(prog)s list --status "REC UNREAD"
              %(prog)s read 3
              %(prog)s delete 3
              %(prog)s delete-all
              %(prog)s tokens
              %(prog)s logs
              %(prog)s logs --token admin --since 2026-05-01T00:00:00Z
              %(prog)s logs --recipient +4512345678 --endpoint /sms/send
              %(prog)s at AT+CSQ
              %(prog)s at "AT+CREG?"
              %(prog)s ports

            Use '%(prog)s <command> --help' for per-command details.
        """),
    )

    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {VERSION}"
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        metavar="URL",
        help=f"Gateway base URL  (default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--key", "-k",
        metavar="KEY",
        help="API key — overrides .token file and SMS_GATEWAY_KEY env var",
    )
    parser.add_argument(
        "--token-file",
        default=str(DEFAULT_TOKEN_FILE),
        metavar="FILE",
        help=f"Token file  (default: {DEFAULT_TOKEN_FILE})",
    )
    parser.add_argument(
        "--json", "-j",
        action="store_true",
        help="Print raw JSON output (useful for piping to jq)",
    )

    sub = parser.add_subparsers(dest="command", title="commands", metavar="<command>")
    sub.required = True

    # ── health ─────────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "health",
        help="Check modem connectivity  [no auth required]",
        description=textwrap.dedent("""\
            GET /health

            Checks whether the modem is connected and responding to AT commands.
            This is the only endpoint that does not require an API key.
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.set_defaults(func=cmd_health)

    # ── status ─────────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "status",
        help="Show SIM and network status",
        description=textwrap.dedent("""\
            GET /status

            Returns IMSI, CCID (SIM card ID), signal strength in dBm,
            and the network registration state.
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.set_defaults(func=cmd_status)

    # ── send ───────────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "send",
        help="Send an SMS message",
        description=textwrap.dedent("""\
            POST /sms/send

            Send an SMS to a phone number. Messages longer than 160 characters
            are automatically split by the modem (concatenated SMS).
        """),
        epilog=textwrap.dedent("""\
            Examples:
              sms-cli send +4512345678 "Hello!"
              sms-cli send +4512345678 "$(cat message.txt)"
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "to",
        metavar="NUMBER",
        help="Recipient in E.164 format  (e.g. +4512345678)",
    )
    p.add_argument(
        "message",
        metavar="MESSAGE",
        help="SMS text — quote if it contains spaces",
    )
    p.set_defaults(func=cmd_send)

    # ── list ───────────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "list",
        help="List SMS messages stored on the SIM",
        description=textwrap.dedent("""\
            GET /sms[?status=<filter>]

            Lists all SMS messages currently stored on the SIM card.
            Use --status to show only messages with a specific status flag.
        """),
        epilog=textwrap.dedent("""\
            Status values:
              "REC UNREAD"   Received, not yet read
              "REC READ"     Received and read
              "STO UNSENT"   Stored, not yet sent
              "STO SENT"     Stored and sent

            Examples:
              sms-cli list
              sms-cli list --status "REC UNREAD"
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--status",
        metavar="FILTER",
        help='Filter by status, e.g. "REC UNREAD"',
    )
    p.set_defaults(func=cmd_list)

    # ── read ───────────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "read",
        help="Read one SMS by SIM storage index",
        description=textwrap.dedent("""\
            GET /sms/{index}

            Reads one SMS message by its SIM card storage index (1-based).
            Use 'sms-cli list' to see all indexes.
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "index",
        type=int,
        metavar="INDEX",
        help="SIM storage index (1-based integer)",
    )
    p.set_defaults(func=cmd_read)

    # ── delete ─────────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "delete",
        help="Delete one SMS by SIM storage index",
        description=textwrap.dedent("""\
            DELETE /sms/{index}

            Deletes one SMS message by its SIM card storage index.
            Use 'sms-cli list' to see all indexes.
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "index",
        type=int,
        metavar="INDEX",
        help="SIM storage index (1-based integer)",
    )
    p.set_defaults(func=cmd_delete)

    # ── delete-all ─────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "delete-all",
        help="Delete ALL stored SMS messages",
        description=textwrap.dedent("""\
            DELETE /sms

            Deletes all SMS messages from the SIM card storage.
            You will be prompted for confirmation unless --yes is given.
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip the confirmation prompt",
    )
    p.set_defaults(func=cmd_delete_all)

    # ── pin ────────────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "pin",
        help="Show SIM PIN status",
        description=textwrap.dedent("""\
            GET /sim/pin

            Returns whether the SIM is unlocked (READY), waiting for a PIN
            (SIM PIN), or PUK-locked (SIM PUK).
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.set_defaults(func=cmd_pin)

    # ── unlock ─────────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "unlock",
        help="Unlock SIM card with PIN",
        description=textwrap.dedent("""\
            POST /sim/pin

            Submits the SIM PIN to unlock the card. Only needed when the SIM
            PIN was not provided via the SIM_PIN environment variable at startup.
        """),
        epilog="Example:\n  sms-cli unlock 1234",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "pin",
        metavar="PIN",
        help="4–8 digit SIM PIN code",
    )
    p.set_defaults(func=cmd_unlock)

    # ── tokens ─────────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "tokens",
        help="List API tokens and usage statistics",
        description=textwrap.dedent("""\
            GET /tokens

            Shows all configured token names with aggregated usage statistics
            from the persistent request log: total requests, SMS messages sent,
            and the timestamp of the last request.

            Token values are never exposed.
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.set_defaults(func=cmd_tokens)

    # ── logs ───────────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "logs",
        help="Query the persistent request log",
        description=textwrap.dedent("""\
            GET /logs

            Query the full request log stored in the SQLite database.
            Every API call is recorded with its timestamp, token name,
            HTTP method, endpoint, status code, and recipient (SMS sends only).

            All filters are combinable. Results are ordered newest-first.
        """),
        epilog=textwrap.dedent("""\
            Examples:
              sms-cli logs
              sms-cli logs --limit 20
              sms-cli logs --token admin
              sms-cli logs --endpoint /sms/send
              sms-cli logs --recipient +4512345678
              sms-cli logs --since 2026-05-01T00:00:00Z --until 2026-05-07T23:59:59Z
              sms-cli logs --limit 50 --offset 100
              sms-cli logs --json | jq '.entries[] | .recipient' | sort | uniq -c
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--token",
        dest="token_name",
        metavar="NAME",
        help="Filter by token name  (e.g. admin)",
    )
    p.add_argument(
        "--since",
        metavar="ISO8601",
        help="Start timestamp  (e.g. 2026-05-01T00:00:00Z)",
    )
    p.add_argument(
        "--until",
        metavar="ISO8601",
        help="End timestamp    (e.g. 2026-05-07T23:59:59Z)",
    )
    p.add_argument(
        "--recipient",
        metavar="NUMBER",
        help="Filter by recipient phone number",
    )
    p.add_argument(
        "--endpoint",
        metavar="PATH",
        help="Filter by API endpoint path  (e.g. /sms/send)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=50,
        metavar="N",
        help="Max entries to return  (default: 50, max: 1000)",
    )
    p.add_argument(
        "--offset",
        type=int,
        default=0,
        metavar="N",
        help="Entries to skip for pagination  (default: 0)",
    )
    p.set_defaults(func=cmd_logs)

    # ── at ─────────────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "at",
        help="Send a raw AT command to the modem",
        description=textwrap.dedent("""\
            GET /debug/at?cmd=<command>

            Sends an arbitrary AT command directly to the modem via the gateway
            and returns the raw response. Useful for diagnosing modem state.

            Note: this endpoint has full modem access. Treat it as privileged.
        """),
        epilog=textwrap.dedent("""\
            Common AT commands:
              AT          Ping — should return OK
              AT+CSQ      Signal quality (rssi, ber)
              AT+CREG?    Network registration status
              AT+CIMI     Read IMSI
              AT+CCID     Read SIM card ID
              AT+CSCA?    SMS service centre address
              AT+CPMS?    Message storage status (used/total)
              AT+CMGL="ALL"  List all stored SMS (raw)
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "cmd",
        metavar="COMMAND",
        help='AT command to send  (e.g. AT+CSQ)',
    )
    p.set_defaults(func=cmd_at)

    # ── ports ──────────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "ports",
        help="Scan ttyUSB ports for AT-responsive modems",
        description=textwrap.dedent("""\
            GET /debug/ports

            Probes /dev/ttyUSB0 through /dev/ttyUSB7 and reports which ports
            respond to AT commands. Useful for identifying the correct modem port.

            SIM7600E typical layout:
              ttyUSB0  Diagnostic
              ttyUSB1  GPS / NMEA
              ttyUSB2  AT commands  ← default MODEM_PORT
              ttyUSB3  PPP data modem
              ttyUSB4  ADB

            Ports not passed through to the Docker container will show as errors.
            Add them to 'devices:' in docker-compose.yml to expose them.
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.set_defaults(func=cmd_ports)

    return parser


# ── Entry point ────────────────────────────────────────────────────────────────

def _resolve_token(args) -> str:
    if args.key:
        return args.key
    env = os.environ.get("SMS_GATEWAY_KEY", "").strip()
    if env:
        return env
    token_file = Path(args.token_file)
    if token_file.exists():
        value = token_file.read_text().strip()
        if value:
            return value
    return ""


def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()
    token  = _resolve_token(args)
    args.func(args, token)


if __name__ == "__main__":
    main()
