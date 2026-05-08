---
name: rocketgrabber
description: Pull the user's Rocket Money transaction history. Triggers Rocket Money's CSV export (email-mediated), waits for the user to receive the email, then ingests the CSV into the local data/ directory of /Users/andrewnguyen/workspace/rocketgrabber. Use when the user says things like "grab my rocket money transactions", "pull my rocket money data", "fetch latest RM export", "/rocketgrabber", or otherwise wants a fresh CSV of their Rocket Money transactions on disk.
---

# rocketgrabber

End-to-end workflow to pull the user's Rocket Money transactions to disk.

Repo lives at `/Users/andrewnguyen/workspace/rocketgrabber`. It has an editable
Python install with two relevant entrypoints:

- `python -m rocketgrabber.grab` — drives Chromium with the saved Rocket Money session, clicks the CSV-export icon and the "Export all transactions" button. Rocket Money emails a download link; the script does NOT receive the file directly.
- `python -m rocketgrabber.fetch <path-or-url>` — saves a CSV to `data/transactions.csv` plus a timestamped archive under `data/raw/`. Accepts either a local file path (e.g. one in `~/Downloads/` after the user clicks the email link) or an http(s) URL (downloaded via Playwright using the saved session).

There is also `python -m rocketgrabber.login` for re-authenticating when the saved session at `.auth/state.json` expires. The user does this themselves because it's interactive (2FA).

## How to run the workflow

Always work from `/Users/andrewnguyen/workspace/rocketgrabber`. Use `.venv/bin/python` directly so you don't depend on the user's shell having the venv activated.

### Step 1 — preflight

Check that the saved session exists. If it doesn't, the user must log in interactively first.

```
test -f /Users/andrewnguyen/workspace/rocketgrabber/.auth/state.json
```

If missing, tell the user to run `python -m rocketgrabber.login` themselves in their terminal (you cannot, because it requires them to type their password and 2FA in a real Chromium window). Wait for them to confirm before continuing.

### Step 2 — trigger the export

```
cd /Users/andrewnguyen/workspace/rocketgrabber && .venv/bin/python -m rocketgrabber.grab
```

Expected outcome: the script reports "Export sent!" was confirmed on the page. If it fails (selector drift, RM redesign), retry with `--manual` and have the user click through themselves:

```
cd /Users/andrewnguyen/workspace/rocketgrabber && .venv/bin/python -m rocketgrabber.grab --manual
```

If the run says "redirected to login", the session expired — go back to step 1.

### Step 3 — wait for, and ingest, the email

The export email lands at the user's Rocket Money account email (Gmail, in this user's case) within ~5 minutes. **Do not poll Gmail yourself** — there's no programmatic Gmail integration in this repo. Instead:

1. Tell the user: "Rocket Money will email you a download link in a few minutes. When it arrives, click the link in the email — the CSV will land in `~/Downloads/`. Then tell me when it's done."
2. When the user confirms, find the most recent `*.csv` in `~/Downloads/` (Rocket Money's exports usually start with `transactions` or include a date stamp):

```
ls -t ~/Downloads/*.csv 2>/dev/null | head -5
```

3. Confirm with the user which file it is (don't guess if there are multiple recent CSVs from other sources).

4. Ingest:

```
cd /Users/andrewnguyen/workspace/rocketgrabber && .venv/bin/python -m rocketgrabber.fetch <path-to-csv>
```

This writes `data/transactions.csv` (canonical latest) and `data/raw/transactions-<ts>.csv` (per-run archive). It prints the column header and row count — relay both to the user.

### Step 4 — report

Tell the user:
- How many rows were ingested
- The column header so they can sanity-check the export shape
- The path to the canonical file (`data/transactions.csv`) and the archive

## Things to avoid

- **Don't try to scrape RM's GraphQL responses.** Earlier iterations tried this; the canonical CSV button is far simpler and gives RM's official export shape.
- **Don't auto-pick a CSV from `~/Downloads/`** without user confirmation — they may have other CSVs floating around.
- **Don't run `login` yourself.** It opens a headed browser and waits on `input()`; only the user can complete it.
- **Don't push commits or create PRs** as part of this workflow. The skill is for ingesting data, not editing the repo.

## Failure modes you may see

| Symptom | Cause | Fix |
|---|---|---|
| `redirected to login — session expired` | Saved cookies expired | User runs `python -m rocketgrabber.login`, retry |
| `could not find the CSV icon button` | RM redesigned the page | Run with `--manual`, user clicks |
| `popover with 'Export all transactions' didn't appear` | Click missed or timing | Run with `--manual` |
| Email never arrives (15+ min) | RM-side issue | Have user check spam, retry the trigger |
| `data/transactions.csv` row count looks wrong | Wrong file ingested | Verify with the user, re-run `fetch` with the right path |

## Background — why it works this way

Rocket Money has no public API. The web app at `app.rocketmoney.com` has a built-in CSV-export flow on the transactions page: a small icon-only button next to the sort control opens a popover containing an "Export all transactions" button. Clicking that triggers a server-side job; the result is emailed to the account holder as a download link. We can drive the trigger headlessly with Playwright using a persisted session, but the actual CSV download requires either (a) clicking the email link in any browser, or (b) full Gmail integration, which isn't built. The two-step (`grab` → user-clicks-email → `fetch`) is the simplest reliable workflow.
