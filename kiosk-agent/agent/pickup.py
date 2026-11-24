"""Pickup at the kiosk: check a QR code or PIN, open the right doors, report it.

Works offline: the agent keeps a copy of the active codes (HMAC hashes only) on
disk, and pickup reports wait in an outbox until the server can be reached.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .api import ApiClient, ApiError, OfflineError
from .hardware import Hardware

log = logging.getLogger("agent.pickup")

QR_PREFIX = "PL1"


def hash_code(key: str, value: str) -> str:
    """Same as the backend: HMAC-SHA256 with the kiosk's code key."""
    return hmac.new(key.encode(), value.encode(), hashlib.sha256).hexdigest()


def _parse_time(s: str) -> float:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _save_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


class CodeCache:
    """Active pickup codes for this kiosk, refreshed from the server, kept on disk."""

    def __init__(self, code_key: str, path: Path | None = None, clock: Callable[[], float] = time.time):
        self.key = code_key
        self.path = path
        self.clock = clock
        self.lock = threading.Lock()
        self.codes: dict[int, dict] = {}
        self.used: set[int] = set()  # picked up here, maybe not yet confirmed by the server
        if path and path.exists():
            try:
                data = json.loads(path.read_text())
                self.codes = {int(k): v for k, v in data.get("codes", {}).items()}
                self.used = set(data.get("used", []))
            except (ValueError, OSError):
                log.warning("code cache file unreadable, starting empty")

    def _persist(self) -> None:
        if self.path:
            _save_json(self.path, {"codes": self.codes, "used": sorted(self.used)})

    def replace(self, active: list[dict]) -> None:
        """Server's current list wins; codes already used here stay blocked until the server agrees."""
        with self.lock:
            self.codes = {
                c["job_id"]: {
                    "qr": c["qr_token_hash"], "pin": c["pin_hash"],
                    "compartment_ids": c["compartment_ids"], "slots": c["slots"],
                    "expires": _parse_time(c["expires_at"]),
                }
                for c in active
            }
            self.used &= set(self.codes)  # server confirmed the others
            self._persist()

    def refresh(self, api: ApiClient) -> bool:
        try:
            self.replace(api.codes())
            return True
        except (OfflineError, ApiError) as exc:
            log.warning("could not refresh pickup codes: %s", exc)
            return False

    def _valid(self, job_id: int) -> dict | None:
        c = self.codes.get(job_id)
        if c is None or job_id in self.used or c["expires"] <= self.clock():
            return None
        return c

    def match_qr(self, payload: str) -> tuple[int, dict] | None:
        parts = payload.strip().split(":")
        if len(parts) != 3 or parts[0] != QR_PREFIX or not parts[1].isdigit():
            return None
        job_id, token = int(parts[1]), parts[2]
        with self.lock:
            c = self._valid(job_id)
            if c and hmac.compare_digest(c["qr"], hash_code(self.key, token)):
                return job_id, c
        return None

    def match_pin(self, pin: str) -> tuple[int, dict] | None:
        h = hash_code(self.key, pin.strip())
        with self.lock:
            for job_id in self.codes:
                c = self._valid(job_id)
                if c and hmac.compare_digest(c["pin"], h):
                    return job_id, c
        return None

    def mark_used(self, job_id: int) -> None:
        with self.lock:
            self.used.add(job_id)
            self._persist()


class PinLimiter:
    """5 wrong PINs within 10 minutes lock PIN entry for 5 minutes (QR still works)."""

    def __init__(self, max_failures: int = 5, window: float = 600, lockout: float = 300,
                 clock: Callable[[], float] = time.time):
        self.max_failures, self.window, self.lockout = max_failures, window, lockout
        self.clock = clock
        self.failures: list[float] = []
        self.locked_until = 0.0

    def seconds_locked(self) -> int:
        return max(0, int(self.locked_until - self.clock() + 0.999))

    def failure(self) -> None:
        now = self.clock()
        self.failures = [t for t in self.failures if now - t < self.window] + [now]
        if len(self.failures) >= self.max_failures:
            self.locked_until = now + self.lockout
            self.failures.clear()
            log.warning("too many wrong PINs: PIN entry locked for %ds", self.lockout)

    def success(self) -> None:
        self.failures.clear()


class Outbox:
    """Pickup reports that still have to reach the server (survives restarts)."""

    def __init__(self, path: Path | None = None):
        self.path = path
        self.lock = threading.Lock()
        self.items: list[dict] = []
        if path and path.exists():
            try:
                self.items = json.loads(path.read_text())
            except (ValueError, OSError):
                self.items = []

    def add(self, job_id: int, method: str) -> None:
        with self.lock:
            self.items.append({"job_id": job_id, "method": method, "at": time.time()})
            if self.path:
                _save_json(self.path, self.items)

    def flush(self, api: ApiClient) -> int:
        """Send what we can. Returns how many were delivered (or dropped as not applicable)."""
        done = 0
        with self.lock:
            pending = list(self.items)
        for item in pending:
            try:
                api.pickup(item["job_id"], item["method"])
            except OfflineError:
                break  # try again later, keep order
            except ApiError as exc:
                if exc.status >= 500:
                    break  # server trouble: retry later
                log.error("server refused pickup report for job %s: %s", item["job_id"], exc)
            with self.lock:
                self.items.remove(item)
                if self.path:
                    _save_json(self.path, self.items)
            done += 1
        return done


@dataclass
class OpenPickup:
    job_id: int
    method: str
    compartment_ids: list[int]
    slots: list[int]
    opened_at: float = field(default_factory=time.time)


@dataclass
class PickupResult:
    ok: bool
    message: str
    slots: list[int] = field(default_factory=list)
    job_id: int | None = None
    locked_seconds: int = 0

    def as_dict(self) -> dict:
        return {"ok": self.ok, "message": self.message, "slots": self.slots,
                "job_id": self.job_id, "locked_seconds": self.locked_seconds}


class PickupManager:
    DOOR_TIMEOUT = 180.0  # report anyway if a door is left open this long

    def __init__(self, cache: CodeCache, hw: Hardware, outbox: Outbox, limiter: PinLimiter | None = None,
                 clock: Callable[[], float] = time.time):
        self.cache, self.hw, self.outbox = cache, hw, outbox
        self.limiter = limiter or PinLimiter(clock=clock)
        self.clock = clock
        self.open: dict[int, OpenPickup] = {}
        self.lock = threading.Lock()
        self.last: PickupResult | None = None

    def _open(self, job_id: int, c: dict, method: str) -> PickupResult:
        self.cache.mark_used(job_id)  # a code works once, even offline
        for cid in c["compartment_ids"]:
            self.hw.unlock(cid)
        with self.lock:
            self.open[job_id] = OpenPickup(job_id, method, list(c["compartment_ids"]), list(c["slots"]),
                                           opened_at=self.clock())
        slots = ", ".join(f"#{s}" for s in c["slots"])
        word = "Compartment" if len(c["slots"]) == 1 else "Compartments"
        log.info("pickup job %s by %s: opened %s", job_id, method, slots)
        self.last = PickupResult(True, f"{word} {slots} {'is' if len(c['slots']) == 1 else 'are'} open. "
                                       "Take your documents and close the door.", list(c["slots"]), job_id)
        return self.last

    def scan_qr(self, payload: str) -> PickupResult:
        m = self.cache.match_qr(payload)
        if not m:
            log.info("QR not accepted")
            self.last = PickupResult(False, "This code is not valid here, already used, or expired.")
            return self.last
        return self._open(m[0], m[1], "qr")

    def enter_pin(self, pin: str) -> PickupResult:
        locked = self.limiter.seconds_locked()
        if locked:
            self.last = PickupResult(False, "Too many wrong PINs. Try again later or use your QR code.",
                                     locked_seconds=locked)
            return self.last
        if not (pin.isdigit() and len(pin) == 6):
            self.last = PickupResult(False, "The PIN has 6 digits.")
            return self.last
        m = self.cache.match_pin(pin)
        if not m:
            self.limiter.failure()
            self.last = PickupResult(False, "Wrong PIN.", locked_seconds=self.limiter.seconds_locked())
            return self.last
        self.limiter.success()
        return self._open(m[0], m[1], "pin")

    def check_doors(self) -> list[int]:
        """Finish pickups whose doors are all closed again (or timed out). Returns finished job ids."""
        with self.lock:
            if not self.open:
                return []
            pending = list(self.open.values())
        sensors = {s["id"]: s for s in self.hw.sensors()}
        finished = []
        for p in pending:
            closed = all(sensors.get(cid, {}).get("door_closed") for cid in p.compartment_ids)
            timed_out = self.clock() - p.opened_at > self.DOOR_TIMEOUT
            if closed or timed_out:
                if timed_out and not closed:
                    log.warning("job %s: door still open after %.0fs", p.job_id, self.DOOR_TIMEOUT)
                left = [cid for cid in p.compartment_ids if sensors.get(cid, {}).get("paper_present")]
                if left:
                    log.warning("job %s: paper still inside compartment(s) %s", p.job_id, left)
                self.outbox.add(p.job_id, p.method)
                with self.lock:
                    self.open.pop(p.job_id, None)
                finished.append(p.job_id)
                log.info("pickup of job %s finished", p.job_id)
        return finished

    def status(self) -> dict:
        with self.lock:
            open_slots = sorted(s for p in self.open.values() for s in p.slots)
        return {"open_slots": open_slots, "pin_locked_seconds": self.limiter.seconds_locked(),
                "pending_reports": len(self.outbox.items), "active_codes": len(self.cache.codes)}
