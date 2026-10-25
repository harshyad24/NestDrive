#!/bin/bash
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "Install Python 3 from https://www.python.org/downloads/ or run: xcode-select --install"
  exit 1
fi
if [ ! -x ".venv/bin/python" ]; then
  python3 -m venv .venv || { echo "Setup failed. Check the error above."; exit 1; }
fi
.venv/bin/python -m pip install -r requirements.txt || { echo "Setup failed. Check the error above."; exit 1; }
export NESTDRIVE_HOST="${NESTDRIVE_HOST:-127.0.0.1}"
exec .venv/bin/python -u nestdrive_complete.py
