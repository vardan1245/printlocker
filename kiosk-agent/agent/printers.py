"""Printer drivers: a simulator and a real CUPS driver (same interface)."""
from __future__ import annotations

import logging
import re
import subprocess
import tempfile
import threading
import time
from typing import Callable

log = logging.getLogger("agent.printer")


class PrintError(Exception):
    pass


class PrinterDriver:
    """print_pdf blocks until the printer has finished (or raises PrintError)."""

    def __init__(self, printer_id: int, queue: str):
        self.printer_id = printer_id
        self.queue = queue
        # Set after a jam or failed delivery: paper may still be in the printer or on the
        # elevator tray, so this column takes no new jobs until an operator clears it.
        self.blocked: str | None = None

    def clear_block(self) -> None:
        self.blocked = None

    def print_pdf(self, pdf: bytes, *, sheets: int, duplex: bool, title: str) -> None:
        raise NotImplementedError

    def report(self) -> dict:
        """Status for the heartbeat: {'id', 'status', ...}."""
        return {"id": self.printer_id, "status": "error" if self.blocked else "ok"}


class SimPrinter(PrinterDriver):
    """Pretends to print: one sheet every `seconds_per_sheet` × speed.

    `on_sheet(printer_id)` is called per printed sheet so the hardware simulator
    can stack paper on the elevator tray.
    """

    def __init__(self, printer_id: int, queue: str, speed: float = 1.0,
                 seconds_per_sheet: float = 0.15, on_sheet: Callable[[int], None] | None = None):
        super().__init__(printer_id, queue)
        self.speed = speed
        self.seconds_per_sheet = seconds_per_sheet
        self.on_sheet = on_sheet
        self.fail_next: str | None = None  # set to an error message to simulate a jam
        self.paper_ok = True
        self.toner_ok = True
        self.state = {"busy": False, "title": None, "printed": 0, "total": 0, "error": None}
        self._lock = threading.Lock()

    def print_pdf(self, pdf: bytes, *, sheets: int, duplex: bool, title: str) -> None:
        if not pdf.startswith(b"%PDF"):
            raise PrintError("not a PDF")
        if not self.paper_ok:
            raise PrintError("printer is out of paper")
        with self._lock:
            self.state.update(busy=True, title=title, printed=0, total=sheets, error=None)
            try:
                for i in range(sheets):
                    if self.fail_next and i >= sheets // 2:
                        msg, self.fail_next = self.fail_next, None
                        self.state["error"] = msg
                        raise PrintError(msg)
                    if self.speed:
                        time.sleep(self.seconds_per_sheet * self.speed)
                    self.state["printed"] = i + 1
                    if self.on_sheet:
                        self.on_sheet(self.printer_id)
            finally:
                self.state["busy"] = False

    def clear_block(self) -> None:
        super().clear_block()
        self.state["error"] = None

    def report(self) -> dict:
        bad = self.blocked or self.state["error"] or not self.paper_ok
        return {"id": self.printer_id, "status": "error" if bad else "ok",
                "paper_ok": self.paper_ok, "toner_ok": self.toner_ok}


class CupsPrinter(PrinterDriver):
    """Prints through CUPS with `lp` and waits until the job leaves the queue."""

    _REQ = re.compile(r"request id is (\S+)")

    def __init__(self, printer_id: int, queue: str, run=subprocess.run, poll: float = 2.0):
        super().__init__(printer_id, queue)
        self._run = run
        self.poll = poll

    def print_pdf(self, pdf: bytes, *, sheets: int, duplex: bool, title: str) -> None:
        sides = "two-sided-long-edge" if duplex else "one-sided"
        with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
            f.write(pdf)
            f.flush()
            res = self._run(["lp", "-d", self.queue, "-t", title, "-o", f"sides={sides}",
                             "-o", "media=A4", f.name], capture_output=True, text=True)
            if res.returncode != 0:
                raise PrintError(f"lp failed: {res.stderr.strip() or res.stdout.strip()}")
            m = self._REQ.search(res.stdout)
            if not m:
                raise PrintError(f"unexpected lp output: {res.stdout.strip()}")
            job_id = m.group(1)
            # lp has copied the file into the spool; we can wait without it.
        deadline = time.monotonic() + 60 + 3 * sheets
        while time.monotonic() < deadline:
            pending = self._run(["lpstat", "-W", "not-completed", "-o", self.queue],
                                capture_output=True, text=True).stdout
            if job_id not in pending:
                break
            if self._disabled():
                raise PrintError(f"printer {self.queue} stopped (jam, no paper or offline)")
            time.sleep(self.poll)
        else:
            raise PrintError(f"print job {job_id} timed out")
        done = self._run(["lpstat", "-W", "completed", "-o", self.queue],
                         capture_output=True, text=True).stdout
        if job_id not in done:
            raise PrintError(f"print job {job_id} was cancelled or aborted")

    def _disabled(self) -> bool:
        out = self._run(["lpstat", "-p", self.queue], capture_output=True, text=True).stdout
        return "disabled" in out

    def report(self) -> dict:
        try:
            return {"id": self.printer_id, "status": "error" if self.blocked or self._disabled() else "ok"}
        except OSError:
            return {"id": self.printer_id, "status": "offline"}
