"""Scrape Rocket Money transactions via Playwright.

Loads the persisted browser session, opens the transactions page, listens
for JSON responses from internal endpoints, scrolls to load older history,
and upserts everything that looks like a transaction into the SQLite DB.

Usage:
    python -m rocketgrabber.grab           # headless
    python -m rocketgrabber.grab --headed  # watch it work
    python -m rocketgrabber.grab --debug   # log captured response URLs
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any, Iterable

from playwright.sync_api import Response, TimeoutError as PlaywrightTimeoutError, sync_playwright

from . import config, store


# Heuristic: a dict is "transaction-shaped" if it has an id and either an
# amount or a date-like field. Tight enough to filter out account/budget/
# category records that share the response payload.
_ID_KEYS = {"id", "transactionId", "uuid", "_id"}
_AMOUNT_KEYS = {"amount", "amountCents", "value", "signedAmount"}
_DATE_KEYS = {"date", "transactionDate", "postedDate", "authorizedDate", "createdAt"}


def looks_like_transaction(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    keys = obj.keys()
    if not (_ID_KEYS & keys):
        return False
    return bool((_AMOUNT_KEYS & keys) or (_DATE_KEYS & keys))


def find_transactions(node: Any) -> Iterable[dict[str, Any]]:
    """Walk a JSON tree and yield every transaction-shaped dict."""
    if isinstance(node, list):
        for item in node:
            yield from find_transactions(item)
    elif isinstance(node, dict):
        if looks_like_transaction(node):
            yield node
            # Don't recurse into a transaction — its inner objects (like a
            # nested merchant or category record) aren't separate transactions.
            return
        for value in node.values():
            yield from find_transactions(value)


def _scroll_and_load_more(page) -> None:
    """One round of: scroll the most-overflowing element to its bottom, then
    click any visible Load-More / Show-More button."""
    page.evaluate(
        """
        () => {
            // Scroll the window itself
            window.scrollTo(0, document.body.scrollHeight);
            // Find the largest overflow-y scrollable element and scroll it too
            const candidates = Array.from(document.querySelectorAll('*')).filter(el => {
                const s = getComputedStyle(el);
                return (s.overflowY === 'auto' || s.overflowY === 'scroll')
                    && el.scrollHeight > el.clientHeight + 50;
            });
            candidates.sort((a, b) => b.scrollHeight - a.scrollHeight);
            if (candidates[0]) candidates[0].scrollTop = candidates[0].scrollHeight;
        }
        """
    )
    for label in ("Load more", "Show more", "View more", "Older"):
        try:
            button = page.get_by_role("button", name=label, exact=False)
            if button.count() > 0 and button.first.is_visible():
                button.first.click(timeout=1000)
        except (PlaywrightTimeoutError, Exception):
            pass


def run(headed: bool = False, debug: bool = False) -> int:
    if not config.STATE_FILE.exists():
        print(
            f"no saved session at {config.STATE_FILE.relative_to(config.REPO_ROOT)}.\n"
            f"run `python -m rocketgrabber.login` first.",
            file=sys.stderr,
        )
        return 2

    captured: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    last_capture_at = time.monotonic()

    def on_response(response: Response) -> None:
        nonlocal last_capture_at
        try:
            ctype = response.headers.get("content-type", "")
            if "json" not in ctype:
                return
            if "rocketmoney.com" not in response.url and "rocketmoney" not in response.url:
                return
            body = response.json()
        except Exception:
            return
        new_for_this_response = 0
        for tx in find_transactions(body):
            tx_id = next((str(tx[k]) for k in ("id", "transactionId", "uuid", "_id") if k in tx), None)
            if not tx_id or tx_id in seen_ids:
                continue
            seen_ids.add(tx_id)
            captured.append(tx)
            new_for_this_response += 1
        if new_for_this_response:
            last_capture_at = time.monotonic()
            if debug:
                print(f"  + {new_for_this_response} from {response.url}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        context = browser.new_context(storage_state=str(config.STATE_FILE))
        page = context.new_page()
        page.on("response", on_response)

        print(f">> opening {config.TRANSACTIONS_URL}")
        page.goto(config.TRANSACTIONS_URL, wait_until="domcontentloaded")

        # Detect a redirect back to login (expired session).
        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except PlaywrightTimeoutError:
            pass
        if "/login" in page.url or "signin" in page.url:
            print(
                "redirected to login — session expired.\n"
                "run `python -m rocketgrabber.login` again.",
                file=sys.stderr,
            )
            context.close()
            browser.close()
            return 3

        print(">> scrolling to load history")
        for round_idx in range(config.MAX_SCROLL_ROUNDS):
            before = len(captured)
            _scroll_and_load_more(page)
            page.wait_for_timeout(800)
            idle_for = time.monotonic() - last_capture_at
            if debug:
                print(f"  round {round_idx + 1}: captured={len(captured)} idle_for={idle_for:.1f}s")
            if idle_for >= config.IDLE_SECONDS and len(captured) == before:
                # No new responses in a while AND no new tx this round.
                break

        context.close()
        browser.close()

    print(f">> captured {len(captured)} transactions; writing to SQLite")
    with store.connect(config.DB_FILE) as conn:
        inserted, updated = store.upsert(conn, captured)
        total = store.count(conn)
    print(f">> inserted={inserted} updated={updated} db_total={total}")
    print(f">> db: {config.DB_FILE.relative_to(config.REPO_ROOT)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape Rocket Money transactions to SQLite.")
    parser.add_argument("--headed", action="store_true", help="show the browser")
    parser.add_argument("--debug", action="store_true", help="log captured response URLs")
    args = parser.parse_args()
    return run(headed=args.headed, debug=args.debug)


if __name__ == "__main__":
    sys.exit(main())
