"""Interactive login flow.

Opens a real Chromium window pointed at Rocket Money. The user signs in
manually (including 2FA / email codes). When they confirm in the terminal,
the browser's cookies + localStorage are saved to .auth/state.json so future
runs can skip the login.

Run:
    python -m rocketgrabber.login
"""

from __future__ import annotations

import os
import stat
import sys

from playwright.sync_api import sync_playwright

from . import config


def main() -> int:
    config.AUTH_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context_kwargs: dict = {}
        if config.USER_AGENT:
            context_kwargs["user_agent"] = config.USER_AGENT
        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        print(f">> opening {config.LOGIN_URL}")
        page.goto(config.LOGIN_URL)

        print()
        print("Sign in to Rocket Money in the Chromium window.")
        print("Complete any 2FA / email verification.")
        print("When you can see your dashboard, come back here and press Enter.")
        print()
        try:
            input("Press Enter after you're logged in... ")
        except (EOFError, KeyboardInterrupt):
            print("\naborted; no state saved")
            context.close()
            browser.close()
            return 1

        context.storage_state(path=str(config.STATE_FILE))
        # Live session cookies — restrict to owner-read/write.
        try:
            os.chmod(config.STATE_FILE, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        print(f">> saved session to {config.pretty_path(config.STATE_FILE)}")

        context.close()
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
