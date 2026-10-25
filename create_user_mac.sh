#!/bin/bash
cd "$(dirname "$0")" || exit 1
if [ ! -x ".venv/bin/python" ]; then
  echo "Run start_mac.sh once first, then close the server and run this again."
  exit 1
fi
exec .venv/bin/python create_user.py
