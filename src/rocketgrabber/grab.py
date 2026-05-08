"""Download Rocket Money's CSV export.

The web app exposes a CSV-export button on the transactions page. We just
click it and save the file — much simpler than scraping their GraphQL.

Each run writes:
    data/transactions.csv                 — the latest export (overwritten)
    data/raw/transactions-<ts>.csv        — timestamped archive

Usage:
    python -m rocketgrabber.grab           # headless
    python -m rocketgrabber.grab --headed  # watch it work
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
from datetime import datetime, timezone

from playwright.sync_api import (
    Download,
    Locator,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from . import config


MANUAL_TIMEOUT_SECONDS = 300


def _find_csv_button(page) -> Locator | None:
    """Try a sequence of selectors for the CSV-export button. Returns the
    first that exists and is visible, or None."""
    candidates: list[Locator] = [
        page.get_by_role("button", name=re.compile(r"\bcsv\b|export", re.I)),
        page.locator("button[aria-label*='CSV' i]"),
        page.locator("button[aria-label*='export' i]"),
        page.locator("button[title*='CSV' i]"),
        page.locator("a[download][href$='.csv']"),
        # The visible icon sits next to the sort control; this is a last-ditch
        # heuristic against an icon-only button with no accessible name.
        page.locator("button:has(svg):right-of(:text('Sort by'))").first,
    ]
    for loc in candidates:
        try:
            if loc.count() > 0 and loc.first.is_visible():
                return loc.first
        except Exception:
            continue
    return None


def run(headed: bool = False, manual: bool = False) -> int:
    if not config.STATE_FILE.exists():
        print(
            f"no saved session at {config.pretty_path(config.STATE_FILE)}.\n"
            f"run `python -m rocketgrabber.login` first.",
            file=sys.stderr,
        )
        return 2

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    captured_download: list[Download] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not (headed or manual))
        context = browser.new_context(
            storage_state=str(config.STATE_FILE),
            accept_downloads=True,
        )

        # Capture downloads from any page (including popups) opened in this context.
        def attach_handlers(p) -> None:
            p.on("download", lambda d: captured_download.append(d))

        context.on("page", attach_handlers)
        page = context.new_page()
        attach_handlers(page)

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
            print("MANUAL MODE — click the CSV-export button yourself in the")
            print("Chromium window. Complete any dialog Rocket Money shows.")
            print(f"waiting up to {MANUAL_TIMEOUT_SECONDS}s for a download to start...")
            print()
            deadline = time.monotonic() + MANUAL_TIMEOUT_SECONDS
            while not captured_download and time.monotonic() < deadline:
                page.wait_for_timeout(500)
            if not captured_download:
                print("no download fired in the timeout window.", file=sys.stderr)
                context.close()
                browser.close()
                return 5
            download = captured_download[0]
        else:
            button = _find_csv_button(page)
            if button is None:
                print(
                    "could not find the CSV export button on the page.\n"
                    "re-run with --headed to inspect; if Rocket Money moved or "
                    "renamed it, update _find_csv_button() in grab.py.\n"
                    "or use --manual to click it yourself.",
                    file=sys.stderr,
                )
                context.close()
                browser.close()
                return 4

            print(">> clicking CSV export")
            try:
                with page.expect_download(timeout=60_000) as dl_info:
                    button.click()
                download = dl_info.value
            except PlaywrightTimeoutError:
                print(
                    "clicked the button but no download started within 60s.\n"
                    "the click may have opened a confirm dialog — try --manual.",
                    file=sys.stderr,
                )
                context.close()
                browser.close()
                return 5

        latest = config.DATA_DIR / "transactions.csv"
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive = config.DATA_DIR / "raw" / f"transactions-{ts}.csv"
        archive.parent.mkdir(parents=True, exist_ok=True)
        download.save_as(str(archive))
        shutil.copy(archive, latest)

        context.close()
        browser.close()

    with latest.open(encoding="utf-8") as fh:
        header = fh.readline().rstrip("\r\n")
        row_count = sum(1 for _ in fh)
    print(f">> saved   {config.pretty_path(latest)}")
    print(f">> archive {config.pretty_path(archive)}")
    print(f">> columns {header}")
    print(f">> rows    {row_count}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Download Rocket Money's transactions CSV.")
    parser.add_argument("--headed", action="store_true", help="show the browser")
    parser.add_argument(
        "--manual",
        action="store_true",
        help="open headed and wait for you to click the CSV button yourself",
    )
    args = parser.parse_args()
    return run(headed=args.headed, manual=args.manual)


if __name__ == "__main__":
    sys.exit(main())
