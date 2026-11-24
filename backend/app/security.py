from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from cryptography.fernet import Fernet

from .config import get_settings

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def _token(sub: str, kind: str, ttl: timedelta, extra: dict | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": sub, "type": kind, "iat": now, "exp": now + ttl, **(extra or {})}
    return jwt.encode(payload, get_settings().jwt_secret, algorithm="HS256")


def create_access_token(user_id: int, role: str) -> str:
    s = get_settings()
    return _token(str(user_id), "access", timedelta(minutes=s.access_token_minutes), {"role": role})


def create_refresh_token(user_id: int) -> str:
    s = get_settings()
    return _token(str(user_id), "refresh", timedelta(days=s.refresh_token_days))


def decode_token(token: str, expected_type: str) -> dict:
    """Raise jwt.PyJWTError on any problem."""
    data = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    if data.get("type") != expected_type:
        raise jwt.InvalidTokenError("wrong token type")
    return data


# ---- kiosk API keys ----

def new_api_key() -> str:
    return "plk_" + secrets.token_urlsafe(32)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


# ---- small secrets encrypted at rest (PINs, QR tokens, kiosk code keys) ----

def _fernet() -> Fernet:
    key = get_settings().file_key
    if not key:
        raise RuntimeError("PL_FILE_KEY is not set")
    return Fernet(key.encode())


def encrypt_str(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_str(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()
