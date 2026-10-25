import sqlite3
from getpass import getpass
from auth_config import DATABASE, PASSWORD_METHOD
from setup_db import setup

setup()
username = input("Choose a username: ").strip()
if not username:
    raise SystemExit("Username cannot be empty.")
password = getpass("Choose a password: ")
if not password:
    raise SystemExit("Password cannot be empty.")
if password != getpass("Confirm password: "):
    raise SystemExit("Passwords do not match.")

from werkzeug.security import generate_password_hash
try:
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, generate_password_hash(password, method=PASSWORD_METHOD))
        )
    print("Account created. Start NestDrive and sign in.")
except sqlite3.IntegrityError:
    raise SystemExit("That username already exists. Choose another.")
