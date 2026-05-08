"""SQLite ingest from a Rocket Money CSV.

Schema is loose: every row is stored with its full raw mapping as JSON,
plus extracted canonical fields (date / amount / description / category /
account) when the column header matches a known pattern. Dedup uses a
SHA1 of the canonical fields, so re-ingesting an overlapping export
just inserts the new rows.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    row_hash       TEXT PRIMARY KEY,
    date           TEXT,         -- ISO YYYY-MM-DD when extractable
    amount         REAL,
    description    TEXT,
    category       TEXT,
    account        TEXT,
    raw_json       TEXT NOT NULL,
    first_seen_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_transactions_date ON transactions(date);
CREATE INDEX IF NOT EXISTS ix_transactions_account ON transactions(account);
"""

# Header-name patterns for extracting canonical fields. Lowercased,
# stripped. First match wins.
DATE_COLS = ("date", "transaction date", "posted date", "post date")
AMOUNT_COLS = ("amount", "transaction amount")
DESC_COLS = ("description", "name", "merchant", "original description", "payee")
CATEGORY_COLS = ("category", "primary category")
ACCOUNT_COLS = ("account name", "account", "institution name", "institution")


def _pick(row: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for raw_key, value in row.items():
        if raw_key is None:
            continue
        if raw_key.strip().lower() in keys and value not in (None, ""):
            return value
    return None


def _normalize_amount(s: str | None) -> float | None:
    if not s:
        return None
    cleaned = s.replace(",", "").replace("$", "").strip()
    if not cleaned:
        return None
    # Some exports wrap negatives in parentheses, e.g. "(12.34)".
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    try:
        return float(cleaned)
    except ValueError:
        return None


def _normalize_date(s: str | None) -> str | None:
    """Return ISO YYYY-MM-DD on success, None on parse failure. The original
    string is preserved in the row's `raw_json`, so dropping non-conforming
    values here keeps the `date` column strictly sortable + indexable."""
    if not s:
        return None
    raw = s.strip()
    for fmt in (
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%d/%m/%Y",
    ):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _row_hash(date: str | None, amount: float | None, description: str | None,
              category: str | None, account: str | None, occurrence: int) -> str:
    """Stable hash over canonical fields plus a within-day occurrence index
    so two genuinely-identical transactions on the same day (e.g. two $4.50
    coffees at the same shop) get distinct rows.

    Stable across re-ingests of the same export: as long as RM emits rows
    in the same order, the Nth duplicate gets the same hash both times.
    Overlapping-but-different exports (June 1-30 vs June 15-July 15) may
    still double-insert same-day-same-everything transactions if their
    relative order differs — without an RM-supplied id, that's the best
    we can do without dropping real data."""
    canonical = "|".join([
        date or "",
        f"{amount:.4f}" if amount is not None else "",
        (description or "").strip().lower(),
        (category or "").strip().lower(),
        (account or "").strip().lower(),
        str(occurrence),
    ])
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ingest(conn: sqlite3.Connection, csv_path: Path) -> tuple[int, int]:
    """Insert rows from `csv_path` into `transactions`. Returns (inserted, skipped)."""
    inserted = 0
    skipped = 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # Per-CSV occurrence counter: the Nth row matching the same canonical
    # fingerprint within this file gets occurrence=N. See _row_hash.
    occurrence_counter: dict[tuple[str, str, str, str, str], int] = {}
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            date = _normalize_date(_pick(row, DATE_COLS))
            amount = _normalize_amount(_pick(row, AMOUNT_COLS))
            description = _pick(row, DESC_COLS)
            category = _pick(row, CATEGORY_COLS)
            account = _pick(row, ACCOUNT_COLS)
            key = (
                date or "",
                f"{amount:.4f}" if amount is not None else "",
                (description or "").strip().lower(),
                (category or "").strip().lower(),
                (account or "").strip().lower(),
            )
            occurrence = occurrence_counter.get(key, 0)
            occurrence_counter[key] = occurrence + 1
            row_hash = _row_hash(date, amount, description, category, account, occurrence)
            raw_json = json.dumps(row, default=str)
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO transactions
                    (row_hash, date, amount, description, category, account, raw_json, first_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (row_hash, date, amount, description, category, account, raw_json, now),
            )
            if cur.rowcount > 0:
                inserted += 1
            else:
                skipped += 1
    return inserted, skipped


def count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
