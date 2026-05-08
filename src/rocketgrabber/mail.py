"""Poll Gmail for the latest Rocket Money export email and extract its
download link.

Requires `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` in `.env` (or the
process environment). Generate the app password at
https://myaccount.google.com/apppasswords (requires 2FA on the account).

The IMAP search is anchored on `FROM rocketmoney` and a date floor; we
then filter in Python by subject (case-insensitive 'export') and skip
messages older than `since`.

Usage:
    python -m rocketgrabber.mail              # poll up to 10min, print URL
    python -m rocketgrabber.mail --timeout 60 --since-minutes 5
"""

from __future__ import annotations

import argparse
import email
import imaplib
import os
import re
import ssl
import sys
import time
from datetime import datetime, timedelta, timezone
from email.message import Message
from email.utils import parsedate_to_datetime

from . import config


IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

DEFAULT_FROM_PATTERN = "rocketmoney"
DEFAULT_SUBJECT_KEYWORDS = ("export", "csv", "transactions")

URL_PATTERN = re.compile(r'https?://[^\s"\'<>)\]]+')


def load_env() -> None:
    """Best-effort load of KEY=VALUE pairs from .env into os.environ.
    Existing env vars take precedence (we don't override)."""
    if not config.ENV_FILE.exists():
        return
    for raw in config.ENV_FILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip wrapping single or double quotes if symmetric.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def credentials() -> tuple[str | None, str | None]:
    load_env()
    return os.environ.get("GMAIL_ADDRESS"), os.environ.get("GMAIL_APP_PASSWORD")


def _connect() -> imaplib.IMAP4_SSL | None:
    addr, pwd = credentials()
    if not addr or not pwd:
        print(
            "missing GMAIL_ADDRESS or GMAIL_APP_PASSWORD; copy .env.example to .env "
            "and fill them in.",
            file=sys.stderr,
        )
        return None
    ctx = ssl.create_default_context()
    conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=ctx)
    try:
        conn.login(addr, pwd)
    except imaplib.IMAP4.error as exc:
        print(f"IMAP login failed: {exc}", file=sys.stderr)
        return None
    return conn


def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def _extract_export_link(msg: Message) -> str | None:
    text_parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype in ("text/plain", "text/html"):
                text_parts.append(_decode_part(part))
    else:
        text_parts.append(_decode_part(msg))
    body = "\n".join(text_parts)

    candidates = URL_PATTERN.findall(body)
    if not candidates:
        return None

    def score(url: str) -> tuple[int, int]:
        # Lower-is-better. First key prefers RM-or-export-flavored URLs.
        u = url.lower()
        rm_flavored = any(k in u for k in ("rocketmoney", "export", "download", ".csv"))
        return (0 if rm_flavored else 1, len(url))

    return min(candidates, key=score)


def _parse_msg_date(msg: Message) -> datetime | None:
    raw = msg.get("Date")
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def find_export_link(
    timeout_seconds: int = 600,
    poll_interval: int = 15,
    since: datetime | None = None,
    debug: bool = False,
) -> str | None:
    """Poll Gmail for the latest Rocket Money export email; return its
    download URL. Returns None if nothing matches within the timeout."""
    deadline = time.monotonic() + timeout_seconds
    since_dt = since or (datetime.now(timezone.utc) - timedelta(hours=1))
    conn = _connect()
    if conn is None:
        return None

    seen_uids: set[bytes] = set()
    try:
        while True:
            conn.select("INBOX")
            date_str = since_dt.strftime("%d-%b-%Y")
            typ, data = conn.search(None, f'(SINCE "{date_str}" FROM "{DEFAULT_FROM_PATTERN}")')
            if typ != "OK":
                if debug:
                    print(f"  imap search non-OK: {typ}", file=sys.stderr)
                uids: list[bytes] = []
            else:
                uids = (data[0] or b"").split()
            new_uids = [u for u in uids if u not in seen_uids]
            if debug:
                print(f"  imap matched {len(uids)} uids ({len(new_uids)} new)")

            # Newest first: IMAP returns oldest→newest, so reverse.
            for uid in reversed(new_uids):
                seen_uids.add(uid)
                typ, fdata = conn.fetch(uid, "(RFC822)")
                if typ != "OK" or not fdata or not fdata[0]:
                    continue
                raw = fdata[0][1] if isinstance(fdata[0], tuple) else b""
                if not raw:
                    continue
                msg = email.message_from_bytes(raw)

                subject = (msg.get("Subject") or "").lower()
                if not any(k in subject for k in DEFAULT_SUBJECT_KEYWORDS):
                    if debug:
                        print(f"  skip uid={uid.decode()} subject={subject!r}")
                    continue

                msg_dt = _parse_msg_date(msg)
                if msg_dt and msg_dt < since_dt:
                    if debug:
                        print(f"  skip uid={uid.decode()} too old ({msg_dt})")
                    continue

                link = _extract_export_link(msg)
                if link:
                    if debug:
                        print(f"  match uid={uid.decode()} subject={subject!r}")
                    return link

            if time.monotonic() >= deadline:
                return None
            time.sleep(poll_interval)
    finally:
        try:
            conn.close()
        except Exception:
            pass
        try:
            conn.logout()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Find the latest RM export link in Gmail.")
    parser.add_argument("--timeout", type=int, default=600, help="seconds to wait")
    parser.add_argument("--since-minutes", type=int, default=60, help="look back N minutes")
    parser.add_argument("--poll", type=int, default=15, help="poll interval in seconds")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    since = datetime.now(timezone.utc) - timedelta(minutes=args.since_minutes)
    print(
        f">> polling Gmail for RM export "
        f"(timeout={args.timeout}s, since={since.isoformat(timespec='seconds')})",
        file=sys.stderr,
    )
    link = find_export_link(
        timeout_seconds=args.timeout,
        poll_interval=args.poll,
        since=since,
        debug=args.debug,
    )
    if link:
        print(link)
        return 0
    print("no export email found within timeout", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
