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

from urllib.parse import urlparse

from playwright.sync_api import Response, TimeoutError as PlaywrightTimeoutError, sync_playwright

from . import config, store


# Heuristic: a dict is "transaction-shaped" if it has an id and either an
# amount or a *transaction-specific* date field. We deliberately exclude
# `createdAt` (every record has one) so we don't ingest account/budget/
# category objects that share the GraphQL payload.
_ID_KEYS = {"id", "transactionId", "uuid", "_id"}
_AMOUNT_KEYS = {"amount", "amountCents", "value", "signedAmount"}
_DATE_KEYS = {"date", "transactionDate", "postedDate", "authorizedDate"}

# If any of these keys are present, the object is almost certainly an account,
# institution, or other non-transaction record — reject even if it has id+date.
_NON_TRANSACTION_KEYS = {"balance", "currentBalance", "availableBalance", "mask", "routingNumber"}


def looks_like_transaction(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    keys = obj.keys()
    if not (_ID_KEYS & keys):
        return False
    if _NON_TRANSACTION_KEYS & keys:
        return False
    typename = obj.get("__typename")
    if isinstance(typename, str) and "transaction" not in typename.lower() \
            and typename.lower() not in {"node"}:
        # GraphQL types like "Account", "Budget", "Category" — not transactions.
        return False
    return bool((_AMOUNT_KEYS & keys) or (_DATE_KEYS & keys))


def find_transactions(node: Any) -> Iterable[dict[str, Any]]:
    """Walk a JSON tree and yield every transaction-shaped dict.

    We do *not* short-circuit on a positive match: GraphQL payloads sometimes
    wrap transactions inside an envelope that itself has id+date, and a real
    transaction can also contain nested objects we don't care about. Dedup
    happens upstream via seen_ids."""
    if isinstance(node, list):
        for item in node:
            yield from find_transactions(item)
    elif isinstance(node, dict):
        if looks_like_transaction(node):
            yield node
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
        except Exception:
            pass


def run(headed: bool = False, debug: bool = False) -> int:
    if not config.STATE_FILE.exists():
        print(
            f"no saved session at {config.pretty_path(config.STATE_FILE)}.\n"
            f"run `python -m rocketgrabber.login` first.",
            file=sys.stderr,
        )
        return 2

    captured: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    last_capture_at: float = 0.0  # set after navigation, see below

    def _safe_url(u: str) -> str:
        # Strip query + fragment so debug output never leaks ids/tokens.
        try:
            p = urlparse(u)
            return f"{p.scheme}://{p.netloc}{p.path}"
        except Exception:
            return "<unparseable url>"

    def on_response(response: Response) -> None:
        nonlocal last_capture_at
        try:
            host = urlparse(response.url).netloc.lower()
        except Exception:
            return
        if not host.endswith("app.rocketmoney.com"):
            return
        ctype = response.headers.get("content-type", "")
        if "json" not in ctype:
            return
        try:
            body = response.json()
        except Exception as exc:
            # response body may be aborted/non-JSON despite a JSON content-type.
            if debug:
                print(f"  ! parse-fail {_safe_url(response.url)}: {type(exc).__name__}")
            return
        new_for_this_response = 0
        for tx in find_transactions(body):
            tx_id = next(
                (str(tx[k]) for k in ("id", "transactionId", "uuid", "_id") if tx.get(k)),
                None,
            )
            if not tx_id or tx_id in seen_ids:
                continue
            seen_ids.add(tx_id)
            captured.append(tx)
            new_for_this_response += 1
        if new_for_this_response:
            last_capture_at = time.monotonic()
            if debug:
                print(f"  + {new_for_this_response} from {_safe_url(response.url)}")

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

        # Reset the idle clock now that the page has settled. Without this, a
        # slow first paint can trip the idle exit on round 1 with zero captures.
        last_capture_at = time.monotonic()

        print(">> scrolling to load history")
        for round_idx in range(config.MAX_SCROLL_ROUNDS):
            before = len(captured)
            _scroll_and_load_more(page)
            page.wait_for_timeout(800)
            idle_for = time.monotonic() - last_capture_at
            if debug:
                print(f"  round {round_idx + 1}: captured={len(captured)} idle_for={idle_for:.1f}s")
            # Don't allow an idle exit until we've captured at least one tx —
            # otherwise an empty account or a slow API trips the exit early.
            if captured and idle_for >= config.IDLE_SECONDS and len(captured) == before:
                break

        context.close()
        browser.close()

    print(f">> captured {len(captured)} transactions; writing to SQLite")
    with store.connect(config.DB_FILE) as conn:
        inserted, updated = store.upsert(conn, captured)
        total = store.count(conn)
    print(f">> inserted={inserted} updated={updated} db_total={total}")
    print(f">> db: {config.pretty_path(config.DB_FILE)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape Rocket Money transactions to SQLite.")
    parser.add_argument("--headed", action="store_true", help="show the browser")
    parser.add_argument("--debug", action="store_true", help="log captured response URLs")
    args = parser.parse_args()
    return run(headed=args.headed, debug=args.debug)


if __name__ == "__main__":
    sys.exit(main())
