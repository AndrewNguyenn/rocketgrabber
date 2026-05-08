"""Export transactions from SQLite to CSV.

    python -m rocketgrabber.export
"""

from __future__ import annotations

import csv
import sys

from . import config, store


COLUMNS = (
    "id",
    "date",
    "amount",
    "description",
    "merchant",
    "category",
    "account_id",
    "account_name",
    "pending",
    "first_seen_at",
    "updated_at",
)


def main() -> int:
    if not config.DB_FILE.exists():
        print(
            f"no database at {config.pretty_path(config.DB_FILE)}.\n"
            f"run `python -m rocketgrabber.grab` first.",
            file=sys.stderr,
        )
        return 2

    config.CSV_FILE.parent.mkdir(parents=True, exist_ok=True)

    with store.connect(config.DB_FILE) as conn:
        rows = conn.execute(
            f"SELECT {', '.join(COLUMNS)} FROM transactions ORDER BY date DESC, id"
        ).fetchall()

    with config.CSV_FILE.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(COLUMNS)
        writer.writerows(rows)

    print(f">> wrote {len(rows)} rows to {config.pretty_path(config.CSV_FILE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
