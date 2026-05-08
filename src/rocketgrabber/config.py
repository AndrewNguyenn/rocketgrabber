"""Paths, URLs, tunables. One place to change them."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

AUTH_DIR = REPO_ROOT / ".auth"
STATE_FILE = AUTH_DIR / "state.json"

DATA_DIR = REPO_ROOT / "data"
DB_FILE = DATA_DIR / "rocketgrabber.db"
CSV_FILE = DATA_DIR / "transactions.csv"

LOGIN_URL = "https://app.rocketmoney.com/login"
TRANSACTIONS_URL = "https://app.rocketmoney.com/transactions"

# How long to wait for new XHR responses before deciding pagination is done.
IDLE_SECONDS = 4.0

# Hard cap on scroll iterations so a runaway page doesn't loop forever.
MAX_SCROLL_ROUNDS = 200

# Playwright user-agent — leave None to use Chromium's default.
USER_AGENT: str | None = None


def pretty_path(p: Path) -> Path | str:
    """Render a path relative to REPO_ROOT when possible, else absolute."""
    try:
        return p.relative_to(REPO_ROOT)
    except ValueError:
        return p
