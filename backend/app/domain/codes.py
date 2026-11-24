"""Pickup codes: QR tokens and 6-digit PINs.

The kiosk stores only hashes. Hashes are HMAC-SHA256 with a per-kiosk key, so a
kiosk can check a scanned code offline using its own key, and a leaked hash list
from one kiosk is useless without that key.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Callable

QR_PREFIX = "PL1"
PIN_LENGTH = 6


def hash_code(kiosk_key: str, value: str) -> str:
    return hmac.new(kiosk_key.encode(), value.encode(), hashlib.sha256).hexdigest()


def new_qr_token() -> str:
    return secrets.token_urlsafe(16)  # 128 bits


def qr_payload(job_id: int, token: str) -> str:
    return f"{QR_PREFIX}:{job_id}:{token}"


@dataclass(frozen=True)
class ParsedQr:
    job_id: int
    token: str


def parse_qr(payload: str) -> ParsedQr:
    parts = payload.strip().split(":")
    if len(parts) != 3 or parts[0] != QR_PREFIX or not parts[1].isdigit() or not parts[2]:
        raise ValueError("not a PrintLocker QR code")
    return ParsedQr(job_id=int(parts[1]), token=parts[2])


def new_pin(is_taken: Callable[[str], bool], attempts: int = 50) -> str:
    """Random 6-digit PIN that `is_taken` says is not used by another active job."""
    for _ in range(attempts):
        pin = f"{secrets.randbelow(10 ** PIN_LENGTH):0{PIN_LENGTH}d}"
        if not is_taken(pin):
            return pin
    raise RuntimeError("could not find a free PIN")
