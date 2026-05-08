"""Trigger Rocket Money's CSV export.

Rocket Money's CSV export is email-mediated, not a direct download:
the transactions page has a CSV icon that opens a popover; clicking
"Export all transactions" sends an email with a download link a few
minutes later. This script automates the trigger.

After running, check the email tied to your account and click the
download link. Save the file as `data/transactions.csv`.

Usage:
    python -m rocketgrabber.grab           # headless, clicks through
    python -m rocketgrabber.grab --headed  # watch it work
    python -m rocketgrabber.grab --manual  # click the buttons yourself
"""

from __future__ import annotations

import argparse
import re
import sys

from playwright.sync_api import (
    Locator,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from . import config


MANUAL_TIMEOUT_SECONDS = 300


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


def run(headed: bool = False, manual: bool = False) -> int:
    if not config.STATE_FILE.exists():
        print(
            f"no saved session at {config.pretty_path(config.STATE_FILE)}.\n"
            f"run `python -m rocketgrabber.login` first.",
            file=sys.stderr,
        )
        return 2

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
            except PlaywrightTimeoutError:
                print(
                    "clicked through but never saw the 'Export sent!' confirmation.\n"
                    "the export may still be queued; check your email.",
                    file=sys.stderr,
                )
                # Don't fail hard — RM may have changed copy.

        context.close()
        browser.close()

    print()
    print(">> export triggered. Rocket Money will email a download link to the")
    print("   address on your account in a few minutes. When it arrives:")
    print()
    print("   - click the link in the email; the CSV downloads to ~/Downloads/")
    print("   - then run: python -m rocketgrabber.fetch ~/Downloads/<file>.csv")
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
