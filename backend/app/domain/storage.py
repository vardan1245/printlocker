"""Encrypted file storage for uploaded PDFs.

Two backends with the same interface (save / load / delete / exists):
- DbFileStore: rows in the database (default). Survives restarts and redeploys on hosts with an
  ephemeral filesystem, and needs no extra service. PDFs are capped (max_pdf_mb) and deleted once a job
  is collected, expired, failed or cancelled, so the table stays small.
- EncryptedFileStore: files on local disk (docker-compose with a volume, or a kiosk-side cache).
Both encrypt with Fernet before anything is written.
"""
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


class DbFileStore:
    """Encrypted blobs in a table. `model` is the ORM class (name, data, size, created_at)."""

    def __init__(self, session_factory, key: str | bytes, model):
        self._sessions = session_factory
        self._fernet = Fernet(key if isinstance(key, bytes) else key.encode())
        self._model = model

    def save(self, data: bytes) -> tuple[str, str]:
        name = f"{secrets.token_hex(16)}.pdf.enc"
        with self._sessions() as db:  # own transaction: committed before the job row that points to it
            db.add(self._model(name=name, data=self._fernet.encrypt(data), size=len(data)))
            db.commit()
        return name, hashlib.sha256(data).hexdigest()

    def load(self, name: str) -> bytes:
        with self._sessions() as db:
            row = db.get(self._model, name)
            if row is None:
                raise FileNotFoundError(name)
            return self._fernet.decrypt(row.data)

    def delete(self, name: str) -> None:
        with self._sessions() as db:
            row = db.get(self._model, name)
            if row is not None:
                db.delete(row)
                db.commit()

    def exists(self, name: str) -> bool:
        with self._sessions() as db:
            return db.get(self._model, name) is not None
