---
name: rocketgrabber
description: Pull the user's Rocket Money transaction history end-to-end. Triggers Rocket Money's CSV export (email-mediated), polls Gmail for the export link if credentials are configured, downloads and ingests into local SQLite + CSV under /Users/andrewnguyen/workspace/rocketgrabber/data/. Use when the user says things like "grab my rocket money transactions", "pull my rocket money data", "fetch latest RM export", "/rocketgrabber".
---

# rocketgrabber

End-to-end workflow to pull the user's Rocket Money transactions to disk.

Repo lives at `/Users/andrewnguyen/workspace/rocketgrabber`. Editable Python install with these entrypoints:

- `python -m rocketgrabber.login` — interactive (headed Chromium + 2FA). User must run this themselves.
- `python -m rocketgrabber.grab` — drives Chromium to trigger RM's CSV-export email. If `GMAIL_ADDRESS` + `GMAIL_APP_PASSWORD` are present in `.env`, also polls Gmail for the resulting email link, downloads the CSV, and ingests into SQLite. End-to-end in one command.
- `python -m rocketgrabber.fetch <path-or-url>` — manual ingest path. Accepts a local CSV file or an http(s) URL. Copies to `data/transactions.csv` + a timestamped archive, then ingests into `data/rocketgrabber.db`.
- `python -m rocketgrabber.mail` — standalone Gmail poll; prints the latest export link to stdout. Useful for debugging.

## How to run the workflow

Always work from `/Users/andrewnguyen/workspace/rocketgrabber`. Use `.venv/bin/python` directly so you don't depend on the user's shell having the venv activated.

### Step 1 — preflight

```
test -f /Users/andrewnguyen/workspace/rocketgrabber/.auth/state.json && echo "session ok" || echo "no session"
```

If "no session": tell the user to run `python -m rocketgrabber.login` themselves in their terminal (you cannot — it requires interactive credentials + 2FA in a real Chromium window). Wait for them to confirm before continuing.

### Step 2 — try the auto flow first

```
cd /Users/andrewnguyen/workspace/rocketgrabber && .venv/bin/python -m rocketgrabber.grab
```

There are three outcomes:

1. **Full auto succeeds.** You'll see `>> sqlite inserted=N skipped=M db_total=K`. Done — relay the numbers to the user.

2. **Trigger succeeds but Gmail isn't configured** (`>> Gmail credentials not configured`). The script printed instructions to fall back to manual fetch. Go to step 3.

3. **Trigger succeeds but Gmail polling times out** (`no export email arrived within the timeout`). The export email may still arrive late, or the Gmail filter is too narrow. Go to step 3 or step 4.

### Step 3 — manual fetch (Gmail not set up, or auto timed out)

Tell the user: "Rocket Money will email a download link to the address on your account in a few minutes. Click the link in the email — the CSV will land in `~/Downloads/`. Tell me when it's done."

Once they confirm, find the most recent `*.csv` in `~/Downloads/`:

```
ls -t ~/Downloads/*.csv 2>/dev/null | head -5
```

Confirm with the user which file is the RM export (don't guess if there are multiple recent CSVs).

```
cd /Users/andrewnguyen/workspace/rocketgrabber && .venv/bin/python -m rocketgrabber.fetch <path-to-csv>
```

This copies to `data/transactions.csv`, archives under `data/raw/transactions-<ts>.csv`, and ingests into SQLite.

### Step 4 — set up Gmail auto for next time (optional)

If the user wants full automation going forward and hasn't set up `.env`:

1. Have them generate an app password at https://myaccount.google.com/apppasswords (requires 2FA on their Google account).
2. `cp .env.example .env` and have them paste the address + app password.
3. `chmod 600 .env` (the file holds an app password — restrict permissions).

Don't write the app password to disk yourself; have the user do it.

### Step 5 — report

Tell the user:
- How many rows were inserted vs deduped (skipped)
- Total rows now in `data/rocketgrabber.db`
- The column header of the latest CSV (so they can sanity-check the export shape)
- Path: `data/transactions.csv` is the canonical latest

## Things to avoid

- **Don't run `login` yourself** — it opens a headed browser and waits on `input()`; only the user can complete it.
- **Don't auto-pick a CSV from `~/Downloads/`** without user confirmation.
- **Don't try to scrape RM's GraphQL responses.** An earlier iteration tried this; the canonical CSV-button flow is simpler and gives RM's official export shape.
- **Don't push commits or create PRs** as part of this workflow. The skill ingests data; it doesn't edit the repo.
- **Don't write the user's Gmail password into a file yourself.** Always have the user paste their app password into `.env`.

## Failure modes you may see

| Symptom | Cause | Fix |
|---|---|---|
| `redirected to login — session expired` | Saved cookies expired | User runs `python -m rocketgrabber.login`, retry |
| `could not find the CSV icon button` | RM redesigned the page | Run with `--manual`, user clicks |
| `popover with 'Export all transactions' didn't appear` | Click missed or timing | Run with `--manual` |
| `IMAP login failed` | Wrong app password, or 2FA not enabled on Gmail | Regenerate app password, update `.env` |
| `no export email arrived within timeout` | RM-side delay or Gmail filter too narrow | Wait, fall back to manual fetch (step 3) |
| `data/transactions.csv` row count looks wrong | Wrong file ingested | Verify with the user, re-run `fetch` with the right path |

## Background

Rocket Money has no public API. The web app at `app.rocketmoney.com` exposes a CSV-export icon on the transactions page; clicking it opens a popover with "Export all transactions". That triggers a server job that emails a download link — the actual CSV isn't a direct download from the click. We automate the trigger via Playwright, optionally pick up the email link via Gmail IMAP, and ingest the resulting CSV into a local SQLite DB plus a per-run CSV archive.
