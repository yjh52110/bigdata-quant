"""
Run this once to set up the dashboard PIN and generate all secrets.
Secrets are stored directly in the database — no .env file required.

Usage:
    python backend/scripts/generate_secrets.py
"""
import os
import sqlite3
import secrets
import sys

import bcrypt
from cryptography.fernet import Fernet

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.getenv("DB_PATH", os.path.join(BACKEND_DIR, "drivepool.db"))


def setup_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS app_config (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.commit()


def upsert(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_config (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def main() -> None:
    already_exists = os.path.exists(DB_PATH)
    if already_exists:
        print(f"Database found at {DB_PATH}.")
        overwrite = input("Secrets already exist. Regenerate them? [y/N] ").strip().lower()
        if overwrite != "y":
            print("Aborted — existing secrets unchanged.")
            sys.exit(0)

    pin = input("Enter your dashboard PIN: ").strip()
    if not pin:
        print("PIN cannot be empty.")
        sys.exit(1)

    pin_hash = bcrypt.hashpw(pin.encode(), bcrypt.gensalt()).decode()
    jwt_secret = secrets.token_urlsafe(32)
    encryption_key = Fernet.generate_key().decode()

    conn = sqlite3.connect(DB_PATH)
    try:
        setup_table(conn)
        upsert(conn, "dashboard_pin_hash", pin_hash)
        upsert(conn, "jwt_secret", jwt_secret)
        upsert(conn, "encryption_key", encryption_key)
    finally:
        conn.close()

    print(f"\nSecrets saved to {DB_PATH}")
    print("You can now start the backend:\n")
    print("    uvicorn main:app --reload")


if __name__ == "__main__":
    main()
