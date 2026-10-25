#!/data/data/com.termux/files/usr/bin/bash
cd "$(dirname "$0")" || exit 1
python -m pip install -r requirements.txt || exit 1
exec python create_user.py
