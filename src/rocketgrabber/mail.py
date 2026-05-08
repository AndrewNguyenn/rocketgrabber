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

from urllib.parse import urlparse

from . import config


IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

DEFAULT_FROM_PATTERN = "rocketmoney"
DEFAULT_SUBJECT_KEYWORDS = ("export", "csv", "transactions")

URL_PATTERN = re.compile(r'https?://[^\s"\'<>)\]]+')

# Only URLs on these hosts (or true subdomains) are eligible to be picked
# from the email body. Without this, a click-tracker or unsubscribe link
# would happily be handed to a Playwright session carrying the user's
# live Rocket Money cookies — a real CSRF surface.
RM_HOST_ALLOWLIST: tuple[str, ...] = ("rocketmoney.com",)


def is_rm_host(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return False
    if not host:
        return False
    # Strip user-info / port if present.
    host = host.rsplit("@", 1)[-1].split(":", 1)[0]
    return any(host == h or host.endswith("." + h) for h in RM_HOST_ALLOWLIST)


def load_env() -> None:
    """Best-effort load of KEY=VALUE pairs from .env into os.environ.
    Existing env vars take precedence (we don't override).

    Supports `export KEY=VALUE`, single/double-quoted values (whose contents
    are taken literally and the rest of the line ignored), and inline
    `# comment` after an unquoted value."""
    if not config.ENV_FILE.exists():
        return
    for raw in config.ENV_FILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if value.startswith(('"', "'")):
            quote = value[0]
            close = value.find(quote, 1)
            value = value[1:close] if close > 0 else value[1:]
        else:
            # Unquoted: strip an inline `# comment` if present, then whitespace.
            hash_idx = value.find("#")
            if hash_idx >= 0:
                value = value[:hash_idx]
            value = value.rstrip()
        os.environ.setdefault(key, value)


def credentials() -> tuple[str | None, str | None]:
    load_env()
    addr = os.environ.get("GMAIL_ADDRESS")
    pwd = os.environ.get("GMAIL_APP_PASSWORD")
    # Google app passwords display with spaces ("abcd efgh ..."); strip
    # all whitespace so a paste-with-spaces still authenticates.
    if addr is not None:
        addr = addr.strip()
    if pwd is not None:
        pwd = "".join(pwd.split())
    return addr or None, pwd or None


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

    # Hard host filter: only consider URLs on rocketmoney.com (or true
    # subdomains). Click-trackers and unsubscribe links are filtered out.
    candidates = [u for u in URL_PATTERN.findall(body) if is_rm_host(u)]
    if not candidates:
        return None

    def score(url: str) -> tuple[int, int]:
        # Lower-is-better. Prefer URLs that look export-flavored, then
        # shorter ones. Both signals are now within the RM host space.
        u = url.lower()
        export_flavored = any(k in u for k in ("export", "download", ".csv"))
        return (0 if export_flavored else 1, len(url))

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
        # Read-only so polling never marks messages as Seen — the user's
        # inbox shouldn't change behavior because a script glanced at it.
        conn.select("INBOX", readonly=True)
        while True:
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
                # BODY.PEEK[] keeps the \Seen flag untouched even on
                # connections that aren't read-only.
                typ, fdata = conn.fetch(uid, "(BODY.PEEK[])")
                if typ != "OK" or not fdata or not fdata[0]:
                    continue
                raw = fdata[0][1] if isinstance(fdata[0], tuple) else b""
                if not raw:
                    continue
                msg = email.message_from_bytes(raw)

                subject = (msg.get("Subject") or "").lower()
                if not any(k in subject for k in DEFAULT_SUBJECT_KEYWORDS):
                    if debug:
                        # Truncate so debug output the user might paste in
                        # a bug report doesn't leak inbox subject lines.
                        print(f"  skip uid={uid.decode()} subject={subject[:40]!r}")
                    continue

                msg_dt = _parse_msg_date(msg)
                if msg_dt and msg_dt < since_dt:
                    if debug:
                        print(f"  skip uid={uid.decode()} too old ({msg_dt})")
                    continue

                link = _extract_export_link(msg)
                if link:
                    if debug:
                        print(f"  match uid={uid.decode()} subject={subject[:40]!r}")
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
