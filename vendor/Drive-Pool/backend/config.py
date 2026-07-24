import os
import sqlite3

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "drivepool.db"))
CONFIG_DIR = os.getenv("CONFIG_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "config"))
FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:3000")
BACKEND_URL: str = os.getenv("BACKEND_URL", "http://localhost:8000")
JWT_ALGORITHM: str = "HS256"
JWT_EXPIRE_HOURS: int = 24


def _get_config(key: str) -> str:
    if not os.path.exists(DB_PATH):
        raise RuntimeError(
            f"Database not found at {DB_PATH}. "
            "Run: python backend/scripts/generate_secrets.py"
        )
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT value FROM app_config WHERE key = ?", (key,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise RuntimeError(
            f"Config key '{key}' missing from database. "
            "Run: python backend/scripts/generate_secrets.py"
        )
    return row[0]


DASHBOARD_PIN_HASH: str = _get_config("dashboard_pin_hash")
JWT_SECRET: str = _get_config("jwt_secret")
ENCRYPTION_KEY: str = _get_config("encryption_key")
