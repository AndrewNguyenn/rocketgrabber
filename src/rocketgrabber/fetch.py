"""Stash a Rocket Money export CSV under data/.

Use this after `python -m rocketgrabber.grab` triggers the email and you
download the CSV (either by clicking the link in the email, or by passing
a URL to this command). Saves to:

    data/transactions.csv             — canonical latest, overwritten each run
    data/raw/transactions-<ts>.csv    — timestamped archive

Usage:
    # already downloaded by clicking the email link
    python -m rocketgrabber.fetch ~/Downloads/transactions.csv

    # let Playwright fetch it for us using the saved RM session
    python -m rocketgrabber.fetch https://app.rocketmoney.com/some/export/url
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from . import config


def _is_url(s: str) -> bool:
    try:
        u = urlparse(s)
    except Exception:
        return False
    return u.scheme in ("http", "https")


def _download_with_session(url: str, target: Path) -> int:
    from playwright.sync_api import sync_playwright

    if not config.STATE_FILE.exists():
        print(
            f"no saved session at {config.pretty_path(config.STATE_FILE)}.\n"
            f"run `python -m rocketgrabber.login` first.",
            file=sys.stderr,
        )
        return 2

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            storage_state=str(config.STATE_FILE),
            accept_downloads=True,
        )
        page = context.new_page()
        print(f">> fetching {url}")
        try:
            with page.expect_download(timeout=120_000) as dl_info:
                page.goto(url)
            dl_info.value.save_as(str(target))
        except Exception as exc:
            print(f"download failed: {exc}", file=sys.stderr)
            context.close()
            browser.close()
            return 6
        context.close()
        browser.close()
    return 0


def run(source: str) -> int:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    archive_dir = config.DATA_DIR / "raw"
    archive_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = archive_dir / f"transactions-{ts}.csv"

    if _is_url(source):
        rc = _download_with_session(source, archive)
        if rc != 0:
            return rc
    else:
        src = Path(source).expanduser().resolve()
        if not src.is_file():
            print(f"no such file: {src}", file=sys.stderr)
            return 1
        shutil.copy(src, archive)

    latest = config.DATA_DIR / "transactions.csv"
    shutil.copy(archive, latest)

    with latest.open(encoding="utf-8") as fh:
        header = fh.readline().rstrip("\r\n")
        row_count = sum(1 for _ in fh)

    print(f">> saved   {config.pretty_path(latest)}")
    print(f">> archive {config.pretty_path(archive)}")
    print(f">> columns {header}")
    print(f">> rows    {row_count}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Save a Rocket Money export CSV under data/.")
    parser.add_argument("source", help="path to a downloaded CSV, or an http(s) URL")
    args = parser.parse_args()
    return run(args.source)


if __name__ == "__main__":
    sys.exit(main())
