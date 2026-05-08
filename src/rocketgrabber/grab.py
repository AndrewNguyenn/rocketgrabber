"""Trigger Rocket Money's CSV export, and (optionally) fetch the result.

Rocket Money's CSV export is email-mediated: the transactions page has
a CSV icon that opens a popover; clicking "Export all transactions"
sends an email with a download link a few minutes later.

This script:
1. Drives Chromium with the saved session and clicks through the popover.
2. If GMAIL_ADDRESS + GMAIL_APP_PASSWORD are present in .env, polls
   Gmail for the resulting export email, downloads the linked CSV, and
   ingests into SQLite. Otherwise, prints instructions for the user.

Usage:
    python -m rocketgrabber.grab               # auto if .env is set, else manual fallback
    python -m rocketgrabber.grab --no-auto     # only trigger; never poll Gmail
    python -m rocketgrabber.grab --headed      # watch it work
    python -m rocketgrabber.grab --manual      # click the buttons yourself
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta, timezone

from playwright.sync_api import (
    Locator,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from . import config, fetch, mail


MANUAL_TIMEOUT_SECONDS = 300
GMAIL_POLL_TIMEOUT_SECONDS = 600
GMAIL_POLL_INTERVAL_SECONDS = 15


def _find_csv_icon_button(page) -> Locator | None:
    """The CSV trigger is a small icon-only button next to the sort control.
    No accessible label, so we approach by position + DOM structure."""
    candidates: list[Locator] = [
        page.locator("button[aria-label*='CSV' i]"),
        page.locator("button[aria-label*='export' i]"),
        page.locator("button[title*='CSV' i]"),
        # Icon-only button to the right of "Sort by date".
        page.locator("button:has(svg):right-of(:text('Sort by'))"),
    ]
    for loc in candidates:
        try:
            if loc.count() > 0 and loc.first.is_visible():
                return loc.first
        except Exception:
            continue
    return None


def run(headed: bool = False, manual: bool = False, auto: bool = True) -> int:
    if not config.STATE_FILE.exists():
        print(
            f"no saved session at {config.pretty_path(config.STATE_FILE)}.\n"
            f"run `python -m rocketgrabber.login` first.",
            file=sys.stderr,
        )
        return 2

    trigger_started_at = datetime.now(timezone.utc)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not (headed or manual))
        context = browser.new_context(storage_state=str(config.STATE_FILE))
        page = context.new_page()

        print(f">> opening {config.TRANSACTIONS_URL}")
        page.goto(config.TRANSACTIONS_URL, wait_until="domcontentloaded")
        if "/login" in page.url or "signin" in page.url:
            print(
                "redirected to login — session expired.\n"
                "run `python -m rocketgrabber.login` again.",
                file=sys.stderr,
            )
            context.close()
            browser.close()
            return 3

        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeoutError:
            pass

        if manual:
            print()
            print("MANUAL MODE — click the CSV icon, then 'Export all transactions'")
            print("in the Chromium window. Wait for the 'Export sent!' confirmation.")
            print(f"waiting up to {MANUAL_TIMEOUT_SECONDS}s...")
            print()
            try:
                page.get_by_text(re.compile(r"Export sent", re.I)).wait_for(
                    timeout=MANUAL_TIMEOUT_SECONDS * 1000
                )
            except PlaywrightTimeoutError:
                print("never saw 'Export sent!' confirmation.", file=sys.stderr)
                context.close()
                browser.close()
                return 5
        else:
            icon = _find_csv_icon_button(page)
            if icon is None:
                print(
                    "could not find the CSV icon button.\n"
                    "re-run with --manual to click it yourself.",
                    file=sys.stderr,
                )
                context.close()
                browser.close()
                return 4

            print(">> clicking CSV icon")
            icon.click()

            try:
                export_btn = page.get_by_role(
                    "button", name=re.compile(r"export all transactions", re.I)
                )
                export_btn.first.wait_for(timeout=10_000)
                print(">> clicking 'Export all transactions'")
                export_btn.first.click()
            except PlaywrightTimeoutError:
                print(
                    "popover with 'Export all transactions' didn't appear.\n"
                    "Rocket Money may have changed the UI — try --manual.",
                    file=sys.stderr,
                )
                context.close()
                browser.close()
                return 5

            try:
                page.get_by_text(re.compile(r"Export sent", re.I)).wait_for(timeout=20_000)
                export_confirmed = True
            except PlaywrightTimeoutError:
                export_confirmed = False
                print(
                    "clicked through but never saw the 'Export sent!' confirmation.",
                    file=sys.stderr,
                )

            if not export_confirmed and auto:
                print(
                    "in auto mode this is treated as a hard failure — the email "
                    "isn't going to arrive. retry with --manual to see what's "
                    "happening, or with --no-auto to fall back to manual fetch.",
                    file=sys.stderr,
                )
                context.close()
                browser.close()
                return 5
            # Without --auto we accept the soft path: maybe RM changed copy
            # and the export still went; user can fetch manually.

        context.close()
        browser.close()

    print()
    print(">> export triggered.")

    # Auto-fetch path: poll Gmail for the link, then hand to fetch.
    addr, pwd = mail.credentials() if auto else (None, None)
    if auto and addr and pwd:
        # Anchor the search slightly before the trigger to handle clock skew.
        since = trigger_started_at - timedelta(minutes=2)
        print(
            f">> polling {addr} for the export email "
            f"(timeout={GMAIL_POLL_TIMEOUT_SECONDS}s, since={since.isoformat(timespec='seconds')})"
        )
        link = mail.find_export_link(
            timeout_seconds=GMAIL_POLL_TIMEOUT_SECONDS,
            poll_interval=GMAIL_POLL_INTERVAL_SECONDS,
            since=since,
        )
        if not link:
            print(
                "no export email arrived within the timeout. "
                "you can ingest manually once it shows up:\n"
                "    python -m rocketgrabber.fetch <path-or-url>",
                file=sys.stderr,
            )
            return 7
        print(">> got export link; handing to fetch")
        return fetch.run(link)

    if not auto:
        print(">> auto-fetch disabled (--no-auto)")
    else:
        print(
            ">> Gmail credentials not configured; copy .env.example to .env and fill in "
            "GMAIL_ADDRESS + GMAIL_APP_PASSWORD to enable end-to-end mode."
        )
    print(
        ">> when the email arrives, click the link to download the CSV, then run:\n"
        "       python -m rocketgrabber.fetch ~/Downloads/<file>.csv"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Download Rocket Money's transactions CSV.")
    parser.add_argument("--headed", action="store_true", help="show the browser")
    parser.add_argument(
        "--manual",
        action="store_true",
        help="open headed and wait for you to click the CSV button yourself",
    )
    parser.add_argument(
        "--no-auto",
        action="store_true",
        help="don't poll Gmail after triggering, even if credentials are present",
    )
    args = parser.parse_args()
    return run(headed=args.headed, manual=args.manual, auto=not args.no_auto)


if __name__ == "__main__":
    sys.exit(main())
