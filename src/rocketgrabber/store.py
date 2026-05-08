"""SQLite store for transactions.

Schema is intentionally loose: we keep a normalized set of useful columns
plus the full raw JSON, so even if Rocket Money's payload shape drifts we
don't lose data — only the normalized columns might go stale, and they can
be re-derived from `raw_json` later.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id              TEXT PRIMARY KEY,
    date            TEXT,                  -- ISO YYYY-MM-DD when extractable
    amount          REAL,                  -- raw value as Rocket Money emits it; sign convention follows their API. Cents are scaled to dollars when the source key is *Cents.
    description     TEXT,
    merchant        TEXT,
    category        TEXT,
    account_id      TEXT,
    account_name    TEXT,
    pending         INTEGER,               -- 0/1
    raw_json        TEXT NOT NULL,         -- full original payload; source of truth if normalized columns drift
    first_seen_at   TEXT NOT NULL,         -- ISO timestamp; preserved across re-grabs
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_transactions_date ON transactions(date);
CREATE INDEX IF NOT EXISTS ix_transactions_merchant ON transactions(merchant);
"""


# Field-name fallbacks. Rocket Money's payloads aren't published, so we try
# a handful of common shapes and take the first non-empty value.
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("id", "transactionId", "uuid", "_id"),
    "date": ("date", "transactionDate", "postedDate", "authorizedDate", "createdAt"),
    "description": ("description", "name", "displayName", "originalDescription"),
    "merchant": ("merchant", "merchantName", "payee"),
    "category": ("category", "categoryName", "primaryCategory"),
    "account_id": ("accountId", "account_id", "fundingAccountId"),
    "account_name": ("accountName", "account_name", "institutionName"),
    "pending": ("pending", "isPending"),
}

# Amount is special — some shapes use cents-as-int, others use float dollars.
# We track which key we matched so we can scale appropriately.
_AMOUNT_KEYS_DOLLARS: tuple[str, ...] = ("amount", "value", "signedAmount")
_AMOUNT_KEYS_CENTS: tuple[str, ...] = ("amountCents",)


def _pick(obj: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    for key in aliases:
        if key in obj and obj[key] not in (None, ""):
            return obj[key]
    return None


def _normalize_amount(obj: dict[str, Any]) -> float | None:
    raw = _pick(obj, _AMOUNT_KEYS_DOLLARS)
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
    raw = _pick(obj, _AMOUNT_KEYS_CENTS)
    if raw is not None:
        try:
            return float(raw) / 100.0
        except (TypeError, ValueError):
            return None
    return None


def _normalize_pending(raw: Any) -> int | None:
    if raw is None:
        return None
    return 1 if bool(raw) else 0


def _normalize_date(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        # bool is an int subclass — treat True/False as not a date.
        return None
    if isinstance(raw, (int, float)):
        # Reject implausibly small numbers (day-of-month, counter values, etc.)
        # that would otherwise resolve to 1970. 10**9 ~= Sep 2001 in seconds.
        if abs(raw) < 10**9:
            return None
        seconds = raw / 1000.0 if raw > 10**12 else float(raw)
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    s = str(raw)
    # Trim time component if present so the column stays YYYY-MM-DD when possible.
    if "T" in s and len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return s


def normalize(obj: dict[str, Any]) -> dict[str, Any] | None:
    """Map a raw transaction dict to our normalized columns. Returns None if it
    doesn't have the minimum fields we need (id + amount-or-date)."""
    tx_id = _pick(obj, _FIELD_ALIASES["id"])
    if not tx_id:
        return None
    amount = _normalize_amount(obj)
    date = _normalize_date(_pick(obj, _FIELD_ALIASES["date"]))
    if amount is None and date is None:
        return None
    return {
        "id": str(tx_id),
        "date": date,
        "amount": amount,
        "description": _pick(obj, _FIELD_ALIASES["description"]),
        "merchant": _pick(obj, _FIELD_ALIASES["merchant"]),
        "category": _pick(obj, _FIELD_ALIASES["category"]),
        "account_id": _pick(obj, _FIELD_ALIASES["account_id"]),
        "account_name": _pick(obj, _FIELD_ALIASES["account_name"]),
        "pending": _normalize_pending(_pick(obj, _FIELD_ALIASES["pending"])),
    }


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert(conn: sqlite3.Connection, raw_objects: Iterable[dict[str, Any]]) -> tuple[int, int]:
    """Upsert a batch of raw transaction dicts. Returns (inserted, updated)."""
    inserted = 0
    updated = 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for raw in raw_objects:
        norm = normalize(raw)
        if not norm:
            continue
        raw_json = json.dumps(raw, default=str, sort_keys=True)
        cur = conn.execute("SELECT 1 FROM transactions WHERE id = ?", (norm["id"],))
        exists = cur.fetchone() is not None
        if exists:
            conn.execute(
                """
                UPDATE transactions SET
                    date = ?, amount = ?, description = ?, merchant = ?,
                    category = ?, account_id = ?, account_name = ?, pending = ?,
                    raw_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    norm["date"], norm["amount"], norm["description"], norm["merchant"],
                    norm["category"], norm["account_id"], norm["account_name"],
                    norm["pending"], raw_json, now, norm["id"],
                ),
            )
            updated += 1
        else:
            conn.execute(
                """
                INSERT INTO transactions (
                    id, date, amount, description, merchant, category,
                    account_id, account_name, pending, raw_json,
                    first_seen_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    norm["id"], norm["date"], norm["amount"], norm["description"],
                    norm["merchant"], norm["category"], norm["account_id"],
                    norm["account_name"], norm["pending"], raw_json, now, now,
                ),
            )
            inserted += 1
    return inserted, updated


def count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
