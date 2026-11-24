"""Processing one print job: download, print part by part, deliver, report."""
from __future__ import annotations

import hashlib
import logging
import threading

from .api import ApiClient, ApiError, OfflineError
from .hardware import Hardware, HardwareError
from .layout import build_pdf, page_sequence, sheet_count, split_sequence
from .printers import PrintError, PrinterDriver

log = logging.getLogger("agent.worker")


class JobFailed(Exception):
    pass


def process_job(job: dict, api: ApiClient, printer: PrinterDriver, hw: Hardware,
                on_ready=None) -> str:
    """Run one job to 'ready' or 'failed'. Returns the final status."""
    job_id = job["id"]
    try:
        api.set_status(job_id, "printing")
        pdf = api.job_file(job_id)
        if hashlib.sha256(pdf).hexdigest() != job["file_sha256"]:
            raise JobFailed("downloaded file is corrupted (checksum mismatch)")

        seq = page_sequence(job["pages"], job["page_range"], job["copies"], job["duplex"])
        if sheet_count(seq, job["duplex"]) != job["sheets"]:
            raise JobFailed("sheet count does not match the server")
        parts = sorted(job["parts"], key=lambda p: p["part_no"])
        chunks = split_sequence(seq, [p["sheets"] for p in parts], job["duplex"])
        del seq

        for i, (part, faces) in enumerate(zip(parts, chunks, strict=True)):
            last = i == len(parts) - 1
            title = f"PL job {job_id} part {part['part_no']}/{len(parts)}"
            log.info("job %s: printing part %s (%s sheets)", job_id, part["part_no"], part["sheets"])
            printer.print_pdf(build_pdf(pdf, faces), sheets=part["sheets"], duplex=job["duplex"], title=title)
            if last:
                api.set_status(job_id, "delivering")
            log.info("job %s: delivering part %s to slot %s", job_id, part["part_no"], part["slot_no"])
            hw.deliver(job["printer_id"], part["slot_no"], part["sheets"])

        api.set_status(job_id, "ready")
        log.info("job %s: ready", job_id)
        if on_ready:
            on_ready(job_id)  # e.g. fetch the new pickup code right away
        return "ready"
    except (JobFailed, PrintError, HardwareError, ValueError, ApiError, OfflineError) as exc:
        log.error("job %s failed: %s", job_id, exc)
        if isinstance(exc, (PrintError, HardwareError)):
            printer.blocked = f"job {job_id}: {exc}"
            log.error("printer %s blocked until an operator clears it", printer.printer_id)
        try:
            api.set_status(job_id, "failed", str(exc))
        except (ApiError, OfflineError) as exc2:
            log.error("job %s: could not report failure: %s", job_id, exc2)
        return "failed"


class PrinterWorker(threading.Thread):
    """One per printer: poll for the next queued job and process it."""

    def __init__(self, api: ApiClient, printer: PrinterDriver, hw: Hardware, poll_seconds: float,
                 stop: threading.Event, on_ready=None):
        super().__init__(name=f"printer-{printer.printer_id}", daemon=True)
        self.api, self.printer, self.hw = api, printer, hw
        self.poll_seconds = poll_seconds
        self.stop_event = stop
        self.on_ready = on_ready

    def run(self) -> None:
        while not self.stop_event.is_set():
            if self.printer.blocked:
                self.stop_event.wait(self.poll_seconds)
                continue
            try:
                job = self.api.next_job(self.printer.printer_id)
            except (OfflineError, ApiError) as exc:
                log.warning("printer %s: cannot get jobs: %s", self.printer.printer_id, exc)
                job = None
            if job:
                try:
                    process_job(job, self.api, self.printer, self.hw, self.on_ready)
                except Exception:  # never let one bad job kill the printer loop
                    log.exception("printer %s: unexpected error", self.printer.printer_id)
                continue  # look for the next job straight away
            self.stop_event.wait(self.poll_seconds)
