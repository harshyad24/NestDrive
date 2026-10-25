#!/data/data/com.termux/files/usr/bin/bash
cd "$(dirname "$0")" || exit 1
python -m pip install -r requirements.txt || exit 1
export NESTDRIVE_HOST=127.0.0.1
exec python -u nestdrive_complete.py
