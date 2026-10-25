import hashlib
import os
import secrets
from pathlib import Path

BASE = Path(__file__).resolve().parent
STORAGE = BASE / "storage"
DATABASE = BASE / "data" / "nestdrive.db"
STORAGE.mkdir(parents=True, exist_ok=True)
DATABASE.parent.mkdir(parents=True, exist_ok=True)

KEY_FILE = BASE / ".nestdrive_secret"
if os.environ.get("NESTDRIVE_SECRET"):
    SECRET_KEY = os.environ["NESTDRIVE_SECRET"]
elif KEY_FILE.exists():
    SECRET_KEY = KEY_FILE.read_text().strip()
else:
    SECRET_KEY = secrets.token_hex(32)
    KEY_FILE.write_text(SECRET_KEY)
    try:
        KEY_FILE.chmod(0o600)
    except OSError:
        pass

# Apple's bundled Python is built against LibreSSL, which lacks scrypt.
PASSWORD_METHOD = "scrypt" if hasattr(hashlib, "scrypt") else "pbkdf2:sha256"
