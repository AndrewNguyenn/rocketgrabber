# rocketgrabber

Pull your [Rocket Money](https://www.rocketmoney.com) transaction history to a CSV on
your Mac. Rocket Money has no public API, but the web app has a built-in CSV-export
button — this script just drives Chromium with Playwright, signs in (once,
interactively), and clicks that button on a schedule.

> ⚠️ Personal-use tool for your own account. Don't point it at anyone else's.

## Requirements

- macOS (tested on Apple Silicon, Darwin 24)
- Python 3.9+
- A Rocket Money account

## Setup

```bash
./scripts/setup.sh
```

Creates a `.venv`, installs `rocketgrabber` (editable) and Playwright, and downloads a
Chromium build.

## First run — log in

```bash
source .venv/bin/activate
python -m rocketgrabber.login
```

A real Chromium window opens at `app.rocketmoney.com`. Sign in normally — including any
2FA / email code. When you can see your dashboard, return to the terminal and press
**Enter**. The session (cookies + localStorage) is saved to `.auth/state.json` (chmod
`0600`) so subsequent runs don't need to log in again.

`.auth/` is gitignored. Don't share it.

## Grab transactions

Rocket Money's CSV export is **email-mediated** — clicking the export button in their
UI triggers a server-side job that emails you a download link. There are two ways to
run the pipeline.

### Option A: full auto (recommended)

Set up Gmail credentials once:

1. Generate an app password at https://myaccount.google.com/apppasswords (requires 2FA
   on your Google account).
2. `cp .env.example .env`, fill in `GMAIL_ADDRESS` + `GMAIL_APP_PASSWORD`, then
   `chmod 600 .env`.

Then a single command does the whole pipeline:

```bash
python -m rocketgrabber.grab
```

Steps it runs:

1. Drives Chromium → clicks the CSV icon → "Export all transactions" → waits for
   "Export sent!".
2. Polls Gmail (IMAP, TLS) for the export email — by default up to 10 minutes,
   checking every 15s.
3. Pulls the download link out of the email body.
4. Downloads the CSV via Playwright with the saved RM session.
5. Saves to `data/transactions.csv` (canonical latest) and
   `data/raw/transactions-<utc-ts>.csv` (per-run archive).
6. Ingests into `data/rocketgrabber.db` (SQLite) with idempotent dedup.

If something goes wrong mid-flow (e.g. Gmail polling times out), you can still finish
manually with `fetch` — see Option B.

### Option B: manual ingest

Without `.env`, the trigger half still runs:

```bash
python -m rocketgrabber.grab --no-auto    # or with no .env at all
```

Open the email, click the link, the CSV lands in `~/Downloads/`. Then:

```bash
python -m rocketgrabber.fetch ~/Downloads/transactions.csv
# or pass a URL:
python -m rocketgrabber.fetch "https://app.rocketmoney.com/...export-link..."
```

`fetch` does the same archive + canonical-CSV write + SQLite ingest as the auto flow.

### Other modes

```bash
python -m rocketgrabber.grab --headed     # watch Chromium drive the page
python -m rocketgrabber.grab --manual     # click the buttons yourself
python -m rocketgrabber.mail               # standalone Gmail poll, prints URL
```

### Headed mode

```bash
python -m rocketgrabber.grab --headed
```

Useful when the button's selector breaks or you want to confirm the page state.

### When the session expires

You'll see a redirect back to the login page. Re-run `python -m rocketgrabber.login`.

## Layout

```
rocketgrabber/
├── scripts/setup.sh                    # one-shot environment setup
├── pyproject.toml                      # editable install
├── .env.example                        # template for GMAIL_* secrets (.env is gitignored)
├── src/rocketgrabber/
│   ├── __init__.py
│   ├── config.py                       # paths, URLs
│   ├── login.py                        # interactive login → .auth/state.json
│   ├── grab.py                         # trigger RM's email-CSV export, optionally auto-fetch
│   ├── mail.py                         # Gmail IMAP poll → export link
│   ├── fetch.py                        # path-or-URL → data/transactions.csv + SQLite
│   └── store.py                        # CSV → SQLite ingest with dedup
├── .claude/skills/rocketgrabber/       # Claude Code skill — see "Use as a skill" below
├── data/                               # CSV exports + SQLite DB (gitignored)
└── .auth/                              # Playwright storage state (gitignored)
```

## Use as a Claude Code skill

A skill lives at `.claude/skills/rocketgrabber/SKILL.md` (project-local) and is mirrored
to `~/.claude/skills/rocketgrabber/SKILL.md` (user-global). With it installed, you can
just say things like:

- "grab my rocket money transactions"
- "pull the latest RM data"
- "/rocketgrabber"

…and Claude will: check the saved session, run `grab` to trigger the export, ask you
to paste the email link or downloaded path once it arrives, run `fetch`, and report
the row count.

## Troubleshooting

- **"could not find the CSV export button"**: the selectors in `_find_csv_button()`
  inside `grab.py` are heuristic. Re-run with `--headed`, find the button, and add a
  more specific selector to the candidate list.
- **The click happens but no download starts**: the button may have started rendering a
  confirmation dialog or filter modal. `--headed` will show what's actually happening.
- **Session keeps expiring**: Rocket Money cookies have a finite TTL. Re-run `login`.

## License

MIT. See [LICENSE](LICENSE).
