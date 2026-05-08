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
from datetime import datetime, timezone

from playwright.sync_api import (
    Locator,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from . import config


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


def run(headed: bool = False) -> int:
    if not config.STATE_FILE.exists():
        print(
            f"no saved session at {config.pretty_path(config.STATE_FILE)}.\n"
            f"run `python -m rocketgrabber.login` first.",
            file=sys.stderr,
        )
        return 2

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        context = browser.new_context(
            storage_state=str(config.STATE_FILE),
            accept_downloads=True,
        )
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

        button = _find_csv_button(page)
        if button is None:
            print(
                "could not find the CSV export button on the page.\n"
                "re-run with --headed to inspect; if Rocket Money moved or "
                "renamed it, update _find_csv_button() in grab.py.",
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
                "the button may open a confirm dialog — try --headed.",
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
    args = parser.parse_args()
    return run(headed=args.headed)


if __name__ == "__main__":
    sys.exit(main())
