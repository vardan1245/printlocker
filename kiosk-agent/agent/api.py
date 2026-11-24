"""HTTP client for the backend's /kiosk endpoints (standard library only)."""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger("agent.api")


class ApiError(Exception):
    """The server answered with an error status."""

    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


class OfflineError(Exception):
    """The server could not be reached."""


class ApiClient:
    def __init__(self, base_url: str, kiosk_key: str, timeout: float = 30.0):
        self.base = base_url.rstrip("/")
        self.key = kiosk_key
        self.timeout = timeout

    # ---- low level

    def _request(self, method: str, path: str, body: Any = None, raw: bool = False):
        data = None
        headers = {"X-Kiosk-Key": self.key, "Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read() or b"{}").get("detail", exc.reason)
            except ValueError:
                detail = str(exc.reason)
            raise ApiError(exc.code, str(detail)) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            raise OfflineError(str(exc)) from None
        if raw:
            return payload
        return json.loads(payload) if payload else None

    def _retrying(self, method: str, path: str, body: Any = None, attempts: int = 20, wait: float = 3.0):
        """For calls that must eventually land (status reports). Server errors 4xx are not retried."""
        for i in range(attempts):
            try:
                return self._request(method, path, body)
            except OfflineError as exc:
                if i == attempts - 1:
                    raise
                log.warning("server unreachable (%s), retrying in %.0fs", exc, wait)
                time.sleep(wait)

    # ---- endpoints

    def config(self) -> dict:
        return self._request("GET", "/kiosk/config")

    def heartbeat(self, printers: list[dict], compartments: list[dict]) -> None:
        self._request("POST", "/kiosk/heartbeat", {"printers": printers, "compartments": compartments})

    def next_job(self, printer_id: int) -> dict | None:
        return self._request("GET", f"/kiosk/jobs/next?printer_id={printer_id}")

    def job_file(self, job_id: int) -> bytes:
        return self._request("GET", f"/kiosk/jobs/{job_id}/file", raw=True)

    def set_status(self, job_id: int, status: str, error: str | None = None) -> dict:
        return self._retrying("POST", f"/kiosk/jobs/{job_id}/status", {"status": status, "error": error})

    def recover(self) -> dict:
        """Fail jobs that were mid-print when the agent stopped; returns {'failed_jobs': [...]}."""
        return self._retrying("POST", "/kiosk/recover", {})

    def commands(self) -> list[dict]:
        """Operator commands queued in the admin panel."""
        return self._request("GET", "/kiosk/commands")

    def command_done(self, command_id: int, ok: bool, error: str | None = None) -> dict:
        return self._retrying("POST", f"/kiosk/commands/{command_id}/done", {"ok": ok, "error": error})

    def codes(self) -> list[dict]:
        return self._request("GET", "/kiosk/codes")

    def pickup(self, job_id: int, method: str) -> dict:
        return self._request("POST", "/kiosk/pickup", {"job_id": job_id, "method": method})
