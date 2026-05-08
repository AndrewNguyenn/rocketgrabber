# rocketgrabber

Pull your own [Rocket Money](https://www.rocketmoney.com) transaction history to a local
SQLite DB + CSV. Rocket Money has no public API, so this drives `app.rocketmoney.com`
with Playwright in a real Chromium browser, intercepts the SPA's internal JSON responses,
and persists what comes back.

You sign in once (interactively, including 2FA). After that the saved browser session is
reused for headless re-runs until it expires.

> ⚠️ This is a personal-use tool for your own data. Don't point it at anyone else's
> account. Be a good citizen — don't loop it on a tight schedule. Rocket Money's TOS
> may change; you are responsible for staying within it.

## Requirements

- macOS (tested on Apple Silicon, Darwin 24)
- Python 3.9+
- A Rocket Money account

## Setup

```bash
./scripts/setup.sh
```

This creates a `.venv`, installs `playwright`, and downloads a Chromium build.

## First run — log in

```bash
source .venv/bin/activate
python -m rocketgrabber.login
```

A real Chromium window opens at `app.rocketmoney.com`. Sign in normally — including any
2FA / email code. Once you can see your dashboard, return to the terminal and press
**Enter**. The session (cookies + localStorage) is saved to `.auth/state.json` so
subsequent runs don't need to log in again.

`.auth/` is gitignored. Don't share it.

## Grab transactions

```bash
python -m rocketgrabber.grab
```

This:

1. Loads the saved browser session.
2. Navigates to the transactions view.
3. Listens for JSON responses from Rocket Money's internal endpoints.
4. Scrolls to load older transactions until no new ones arrive.
5. Upserts everything into `data/rocketgrabber.db` (SQLite).

Re-running is safe — transactions are upserted by their Rocket Money id.

### Headed vs headless

The default is headless. If something looks wrong, run headed to watch:

```bash
python -m rocketgrabber.grab --headed
```

### When the session expires

You'll see a redirect back to the login page. Re-run `python -m rocketgrabber.login` and
the saved session is refreshed.

## Export to CSV

```bash
python -m rocketgrabber.export
```

Writes `data/transactions.csv`.

## Layout

```
rocketgrabber/
├── scripts/setup.sh           # one-shot environment setup
├── src/rocketgrabber/
│   ├── __init__.py
│   ├── config.py              # paths, URLs, tunables
│   ├── login.py               # interactive login → .auth/state.json
│   ├── grab.py                # scrape → SQLite
│   ├── store.py               # SQLite schema + upsert
│   └── export.py              # SQLite → CSV
├── data/                      # SQLite DB + CSV (gitignored)
└── .auth/                     # Playwright storage state (gitignored)
```

## How the scrape works

Rocket Money's web app is a SPA that fetches transactions via internal JSON endpoints.
We don't hardcode the endpoint URL (it changes); instead we register a Playwright
`response` handler that captures every JSON response on the transactions page, then
filters for payloads whose objects look like transactions (have an `id` plus an `amount`
and a date-like field). That heuristic is in `src/rocketgrabber/grab.py` — adjust it
there if Rocket Money changes their schema.

For pagination, the script scrolls the transactions list to the bottom in a loop and
stops when no new responses arrive within a short idle window.

## Troubleshooting

- **Login window closes too fast / I didn't get to press Enter.** The login script waits
  for you in the terminal, not in the browser. Don't close the browser window — just
  finish logging in, then return to the terminal and press Enter.
- **No transactions captured.** Run `python -m rocketgrabber.grab --headed --debug` to
  see captured responses. If Rocket Money changed their JSON shape, adjust the
  `looks_like_transaction` heuristic in `grab.py`.
- **Session keeps expiring.** Rocket Money cookies have a finite TTL. Re-run `login`.

## License

MIT. See [LICENSE](LICENSE).
