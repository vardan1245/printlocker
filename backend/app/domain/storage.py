"""Encrypted file storage for uploaded PDFs (local disk in the MVP)."""
from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet


class EncryptedFileStore:
    def __init__(self, root: str | Path, key: str | bytes):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._fernet = Fernet(key if isinstance(key, bytes) else key.encode())

    @staticmethod
    def new_key() -> str:
        return Fernet.generate_key().decode()

    def save(self, data: bytes) -> tuple[str, str]:
        """Encrypt and store; return (relative file name, sha256 of plain data)."""
        name = f"{secrets.token_hex(16)}.pdf.enc"
        tmp = self.root / (name + ".tmp")
        tmp.write_bytes(self._fernet.encrypt(data))
        os.replace(tmp, self.root / name)
        return name, hashlib.sha256(data).hexdigest()

    def load(self, name: str) -> bytes:
        return self._fernet.decrypt(self._path(name).read_bytes())

    def delete(self, name: str) -> None:
        try:
            self._path(name).unlink()
        except FileNotFoundError:
            pass

    def exists(self, name: str) -> bool:
        return self._path(name).exists()

    def _path(self, name: str) -> Path:
        p = (self.root / name).resolve()
        if p.parent != self.root.resolve():
            raise ValueError("invalid file name")
        return p
