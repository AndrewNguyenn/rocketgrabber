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

```bash
python -m rocketgrabber.grab
```

This:

1. Loads the saved browser session.
2. Navigates to the transactions page.
3. Finds and clicks the CSV-export button.
4. Saves the download to:
   - `data/transactions.csv` (overwritten each run — the canonical latest)
   - `data/raw/transactions-<utc-timestamp>.csv` (per-run archive)

Each invocation prints the columns and row count so you can sanity-check the export.

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
├── scripts/setup.sh           # one-shot environment setup
├── src/rocketgrabber/
│   ├── __init__.py
│   ├── config.py              # paths, URLs
│   ├── login.py               # interactive login → .auth/state.json
│   └── grab.py                # navigate + click CSV → data/
├── data/                      # CSV exports (gitignored)
└── .auth/                     # Playwright storage state (gitignored)
```

## Troubleshooting

- **"could not find the CSV export button"**: the selectors in `_find_csv_button()`
  inside `grab.py` are heuristic. Re-run with `--headed`, find the button, and add a
  more specific selector to the candidate list.
- **The click happens but no download starts**: the button may have started rendering a
  confirmation dialog or filter modal. `--headed` will show what's actually happening.
- **Session keeps expiring**: Rocket Money cookies have a finite TTL. Re-run `login`.

## License

MIT. See [LICENSE](LICENSE).
