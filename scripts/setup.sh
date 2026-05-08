#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  echo ">> creating venv"
  python3 -m venv .venv
fi

# shellcheck source=/dev/null
source .venv/bin/activate

echo ">> upgrading pip"
python -m pip install --upgrade pip >/dev/null

echo ">> installing requirements"
pip install -r requirements.txt

echo ">> installing chromium for playwright"
python -m playwright install chromium

echo
echo "done. next:"
echo "  source .venv/bin/activate"
echo "  python -m rocketgrabber.login    # one-time, headed"
echo "  python -m rocketgrabber.grab     # pull transactions"
echo "  python -m rocketgrabber.export   # write data/transactions.csv"
