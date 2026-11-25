from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import fernet_key, get_settings
from ..domain import codes
from ..domain.pdf import InvalidPdf, count_pdf_pages
from ..domain.reservation import (
    CompartmentInfo,
    NoCompartmentAvailable,
    choose_compartments,
)
from ..domain.sheets import InvalidPrintOptions, JobPlan, plan_job
from ..domain.states import (
    ACTIVE,
    TERMINAL,
    USER_CANCELLABLE,
    InvalidTransition,
    JobStatus,
    compartment_state_after,
)
from ..domain.storage import DbFileStore, EncryptedFileStore
from ..models import Compartment, Event, Job, JobCompartment, Kiosk, Printer, PrinterStatus, User
from ..realtime import hub
from ..security import decrypt_str, encrypt_str
from .errors import BadRequest, Conflict, NotFound

_STAMP = {
    JobStatus.QUEUED: "queued_at",
    JobStatus.PRINTING: "printing_at",
    JobStatus.DELIVERING: "delivering_at",
    JobStatus.READY: "ready_at",
    JobStatus.COLLECTED: "collected_at",
}

# The PDF is no longer needed once printing has finished, or once the job ends.
_DELETE_FILE_ON = {JobStatus.DELIVERING} | set(TERMINAL)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def notify(job: Job) -> None:
    """Push the job's new status to the owner's open app sessions."""
    hub.publish_threadsafe(job.user_id, {
        "type": "job",
        "job_id": job.id,
        "status": job.status.value,
        "compartments": [p.compartment.slot_no for p in job.parts],
        "error": job.error,
        "expires_at": job.expires_at.isoformat() if job.expires_at else None,
    })


def get_store() -> EncryptedFileStore | DbFileStore:
    s = get_settings()
    if s.storage_backend == "disk":
        return EncryptedFileStore(s.storage_dir, fernet_key(s.file_key))
    from ..db import SessionLocal
    from ..models import StoredFile

    return DbFileStore(SessionLocal, fernet_key(s.file_key), StoredFile)


@dataclass
class Preview:
    plan: JobPlan
    free_compartments: int
    can_print: bool


def _printer_or_404(db: Session, printer_id: int) -> Printer:
    printer = db.get(Printer, printer_id)
    if printer is None:
        raise NotFound("printer")
    return printer


def _plan(printer: Printer, data: bytes, page_range: str | None, copies: int, duplex: bool) -> JobPlan:
    s = get_settings()
    if duplex and not printer.duplex:
        raise BadRequest("this printer does not support duplex")
    try:
        pages = count_pdf_pages(data, max_bytes=s.max_pdf_mb * 1024 * 1024)
        return plan_job(
            pages,
            page_range,
            copies,
            duplex,
            capacity=s.compartment_capacity_sheets,
            max_sheets=s.max_sheets_per_job,
        )
    except (InvalidPdf, InvalidPrintOptions) as exc:
        raise BadRequest(str(exc)) from exc


def preview_job(
    db: Session, printer_id: int, data: bytes, page_range: str | None, copies: int, duplex: bool
) -> Preview:
    printer = _printer_or_404(db, printer_id)
    plan = _plan(printer, data, page_range, copies, duplex)
    free = sum(1 for c in printer.compartments if c.state.value == "free")
    return Preview(plan=plan, free_compartments=free, can_print=free >= plan.compartments)


def _kiosk_code_key(kiosk: Kiosk) -> str:
    return decrypt_str(kiosk.code_key_enc)


def _pin_taken(db: Session, kiosk_id: int, code_key: str):
    def check(pin: str) -> bool:
        h = codes.hash_code(code_key, pin)
        return (
            db.scalar(
                select(Job.id).where(
                    Job.kiosk_id == kiosk_id, Job.pin_hash == h, Job.status.in_(list(ACTIVE))
                )
            )
            is not None
        )

    return check


def create_job(
    db: Session,
    user: User,
    printer_id: int,
    filename: str,
    data: bytes,
    page_range: str | None,
    copies: int,
    duplex: bool,
    store: EncryptedFileStore | None = None,
) -> Job:
    """Validate, reserve compartments, store the encrypted PDF and queue the job.

    Everything happens in one transaction; if anything fails no compartment
    stays reserved and the stored file is removed.
    """
    printer = _printer_or_404(db, printer_id)
    if printer.status is not PrinterStatus.OK:
        raise Conflict("printer is not available right now")
    plan = _plan(printer, data, page_range, copies, duplex)
    page_range = (page_range or "").strip() or None

    # Lock this printer's compartments so two uploads can't take the same one.
    # (FOR UPDATE on PostgreSQL; SQLite ignores it and serialises writes anyway.)
    rows = list(
        db.scalars(
            select(Compartment)
            .where(Compartment.printer_id == printer.id)
            .order_by(Compartment.slot_no)
            .with_for_update()
        )
    )
    infos = [CompartmentInfo(c.id, c.printer_id, c.slot_no, c.state) for c in rows]
    try:
        picked = choose_compartments(infos, printer.id, plan.compartments)
    except NoCompartmentAvailable as exc:
        raise Conflict(
            f"not enough free compartments at this printer (needs {exc.needed}, free {exc.free})"
        ) from exc
    by_id = {c.id: c for c in rows}

    store = store or get_store()
    file_name, sha = store.save(data)
    try:
        kiosk = db.get(Kiosk, printer.kiosk_id)
        code_key = _kiosk_code_key(kiosk)
        pin = codes.new_pin(_pin_taken(db, kiosk.id, code_key))
        token = codes.new_qr_token()

        job = Job(
            user_id=user.id,
            printer_id=printer.id,
            kiosk_id=kiosk.id,
            original_filename=(filename or "document.pdf")[:255],
            file_path=file_name,
            file_sha256=sha,
            pages=plan.total_pages,
            page_range=page_range,
            selected_pages=plan.selected_pages,
            copies=plan.copies,
            duplex=plan.duplex,
            sheets=plan.sheets,
            status=JobStatus.UPLOADED,
            pin_enc=encrypt_str(pin),
            pin_hash=codes.hash_code(code_key, pin),
            qr_token_enc=encrypt_str(token),
            qr_token_hash=codes.hash_code(code_key, token),
        )
        db.add(job)
        db.flush()
        for part_no, (info, sheets) in enumerate(zip(picked, plan.parts, strict=True), start=1):
            db.add(JobCompartment(job_id=job.id, compartment_id=info.id, part_no=part_no, sheets=sheets))
        db.flush()
        db.refresh(job)
        _apply_transition(db, job, JobStatus.QUEUED, store=store, compartments=[by_id[i.id] for i in picked])
        db.add(Event(kiosk_id=kiosk.id, job_id=job.id, type="job_created",
                     payload={"sheets": plan.sheets, "parts": plan.parts}))
        db.commit()
    except Exception:
        db.rollback()
        store.delete(file_name)
        raise
    db.refresh(job)
    notify(job)
    return job


def _apply_transition(
    db: Session,
    job: Job,
    target: JobStatus,
    *,
    error: str | None = None,
    store: EncryptedFileStore | None = None,
    compartments: list[Compartment] | None = None,
    now: datetime | None = None,
) -> None:
    now = now or _now()
    try:
        new_comp_state = compartment_state_after(job.status, target)
    except InvalidTransition as exc:
        raise Conflict(str(exc)) from exc

    old = job.status
    job.status = target
    if target in _STAMP:
        setattr(job, _STAMP[target], now)
    if target in TERMINAL:
        job.finished_at = now
    if target is JobStatus.READY:
        job.expires_at = now + timedelta(hours=get_settings().pickup_hours)
    if error:
        job.error = error[:500]

    if new_comp_state is not None:
        comps = compartments if compartments is not None else [p.compartment for p in job.parts]
        for c in comps:
            c.state = new_comp_state

    if target in _DELETE_FILE_ON and job.file_path:
        (store or get_store()).delete(job.file_path)
        job.file_path = None

    db.add(Event(kiosk_id=job.kiosk_id, job_id=job.id, type="job_status",
                 payload={"from": old.value, "to": target.value, "error": error}))


def transition_job(
    db: Session, job_id: int, target: JobStatus, error: str | None = None,
    store: EncryptedFileStore | None = None,
) -> Job:
    """Move a job to a new state (used by the kiosk agent in M2, and by admins)."""
    job = db.get(Job, job_id, with_for_update=True)
    if job is None:
        raise NotFound("job")
    _apply_transition(db, job, target, error=error, store=store)
    db.commit()
    db.refresh(job)
    notify(job)
    return job


def get_user_job(db: Session, user: User, job_id: int) -> Job:
    job = db.get(Job, job_id)
    if job is None or job.user_id != user.id:
        raise NotFound("job")
    return job


def list_user_jobs(db: Session, user: User, limit: int = 50) -> list[Job]:
    return list(
        db.scalars(
            select(Job).where(Job.user_id == user.id).order_by(Job.id.desc()).limit(limit)
        )
    )


def cancel_job(db: Session, user: User, job_id: int, store: EncryptedFileStore | None = None) -> Job:
    job = get_user_job(db, user, job_id)
    if job.status not in USER_CANCELLABLE:
        raise Conflict(f"job is '{job.status.value}' and can no longer be cancelled")
    return transition_job(db, job.id, JobStatus.CANCELLED, store=store)


@dataclass
class PickupCodes:
    qr: str
    pin: str
    compartments: list[int]
    expires_at: datetime


def pickup_codes(db: Session, user: User, job_id: int) -> PickupCodes:
    job = get_user_job(db, user, job_id)
    if job.status is not JobStatus.READY:
        raise Conflict(f"job is '{job.status.value}', codes are shown when it is ready")
    return PickupCodes(
        qr=codes.qr_payload(job.id, decrypt_str(job.qr_token_enc)),
        pin=decrypt_str(job.pin_enc),
        compartments=[p.compartment.slot_no for p in job.parts],
        expires_at=job.expires_at,
    )


def expire_due_jobs(db: Session, now: datetime | None = None) -> int:
    """Mark ready jobs past their pickup time as expired. Returns how many."""
    now = now or _now()
    due = list(
        db.scalars(
            select(Job).where(Job.status == JobStatus.READY, Job.expires_at <= now).with_for_update()
        )
    )
    for job in due:
        _apply_transition(db, job, JobStatus.EXPIRED, now=now)
        db.add(Event(kiosk_id=job.kiosk_id, job_id=job.id, type="operator_task",
                     payload={"task": "empty_compartments",
                              "compartments": [p.compartment_id for p in job.parts]}))
    db.commit()
    for job in due:
        notify(job)
    return len(due)


# ---------------------------------------------------------------- kiosk side

KIOSK_TARGETS = frozenset({JobStatus.PRINTING, JobStatus.DELIVERING, JobStatus.READY, JobStatus.FAILED})


def kiosk_job(db: Session, kiosk: Kiosk, job_id: int) -> Job:
    job = db.get(Job, job_id)
    if job is None or job.kiosk_id != kiosk.id:
        raise NotFound("job")
    return job


def next_job(db: Session, kiosk: Kiosk, printer_id: int | None = None) -> Job | None:
    """Oldest queued job for this kiosk (optionally one printer)."""
    q = select(Job).where(Job.kiosk_id == kiosk.id, Job.status == JobStatus.QUEUED)
    if printer_id is not None:
        q = q.where(Job.printer_id == printer_id)
    return db.scalar(q.order_by(Job.id).limit(1))


def load_job_pdf(db: Session, kiosk: Kiosk, job_id: int, store: EncryptedFileStore | None = None) -> bytes:
    job = kiosk_job(db, kiosk, job_id)
    if job.status not in (JobStatus.QUEUED, JobStatus.PRINTING) or not job.file_path:
        raise Conflict(f"job is '{job.status.value}', its file is no longer available")
    return (store or get_store()).load(job.file_path)


def kiosk_transition(
    db: Session, kiosk: Kiosk, job_id: int, target: JobStatus, error: str | None = None,
    store: EncryptedFileStore | None = None,
) -> Job:
    if target not in KIOSK_TARGETS:
        raise Conflict(f"a kiosk cannot set status '{target.value}'")
    job = kiosk_job(db, kiosk, job_id)
    if job.status is target:  # agent retried after a network error: already applied
        return job
    return transition_job(db, job.id, target, error, store=store)


def active_codes(db: Session, kiosk: Kiosk) -> list[Job]:
    """Ready jobs on this kiosk; the agent caches their code hashes for offline pickup."""
    return list(
        db.scalars(
            select(Job).where(Job.kiosk_id == kiosk.id, Job.status == JobStatus.READY).order_by(Job.id)
        )
    )


def report_pickup(db: Session, kiosk: Kiosk, job_id: int, method: str) -> Job:
    """Kiosk says the user took the paper. Idempotent (offline reports may be resent)."""
    job = kiosk_job(db, kiosk, job_id)
    if job.status is JobStatus.COLLECTED:
        return job
    if job.status not in (JobStatus.READY, JobStatus.EXPIRED):
        raise Conflict(f"job is '{job.status.value}', it cannot be collected")
    db.add(Event(kiosk_id=kiosk.id, job_id=job.id, type="pickup", payload={"method": method}))
    return transition_job(db, job.id, JobStatus.COLLECTED)


def recover_interrupted(db: Session, kiosk: Kiosk) -> list[Job]:
    """Called by an agent when it starts: jobs it was printing or delivering can't be finished."""
    stuck = list(db.scalars(
        select(Job).where(Job.kiosk_id == kiosk.id,
                          Job.status.in_([JobStatus.PRINTING, JobStatus.DELIVERING])).order_by(Job.id)))
    out = []
    for job in stuck:
        out.append(transition_job(db, job.id, JobStatus.FAILED,
                                  error="interrupted: the kiosk restarted during this job"))
    return out
