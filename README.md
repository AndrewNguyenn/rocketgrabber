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
UI triggers a server-side job that emails you a download link. So this is two steps:

### Step 1 — trigger the export

```bash
python -m rocketgrabber.grab
```

This drives Chromium with the saved session, clicks the CSV icon, then clicks
"Export all transactions" in the popover, then waits for the "Export sent!" toast.

If the auto-click fails (RM redesigns the page), use manual mode:

```bash
python -m rocketgrabber.grab --manual
```

### Step 2 — ingest the CSV

A few minutes later, Rocket Money emails a download link. Click the link; the CSV
lands in `~/Downloads/`. Then:

```bash
python -m rocketgrabber.fetch ~/Downloads/transactions.csv
```

This writes:
- `data/transactions.csv` — canonical latest, overwritten each run
- `data/raw/transactions-<utc-timestamp>.csv` — per-run archive

It also prints the column header and row count so you can sanity-check.

`fetch` also accepts an http(s) URL, in which case it uses Playwright with the saved
session to download:

```bash
python -m rocketgrabber.fetch "https://app.rocketmoney.com/...export-link..."
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
├── src/rocketgrabber/
│   ├── __init__.py
│   ├── config.py                       # paths, URLs
│   ├── login.py                        # interactive login → .auth/state.json
│   ├── grab.py                         # trigger RM's email-CSV export
│   └── fetch.py                        # ingest a downloaded CSV → data/
├── .claude/skills/rocketgrabber/       # Claude Code skill — see "Use as a skill" below
├── data/                               # CSV exports (gitignored)
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
