from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from cryptography.fernet import Fernet
from fastapi import Cookie, HTTPException, status
from jose import JWTError, jwt

import config

_fernet = Fernet(config.ENCRYPTION_KEY.encode())


def encrypt_token(token: str) -> str:
    return _fernet.encrypt(token.encode()).decode()


def decrypt_token(token: str) -> str:
    return _fernet.decrypt(token.encode()).decode()


def verify_pin(pin: str) -> bool:
    return bcrypt.checkpw(pin.encode(), config.DASHBOARD_PIN_HASH.encode())


def create_access_token(data: dict) -> str:
    payload = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(hours=config.JWT_EXPIRE_HOURS)
    payload["exp"] = expire
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def verify_token(access_token: Optional[str] = Cookie(default=None)) -> dict:
    if not access_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = jwt.decode(access_token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
