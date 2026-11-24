"""Operator views and actions."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.states import CompartmentState, JobStatus
from ..models import (
    CommandStatus,
    Compartment,
    Event,
    Job,
    JobCompartment,
    Kiosk,
    KioskCommand,
    KioskSnapshot,
    Printer,
)
from .errors import BadRequest, NotFound
from .kiosks import effective_status

COMMAND_TYPES = {"unlock", "clear_printer"}
_HOLDING = [JobStatus.QUEUED, JobStatus.PRINTING, JobStatus.DELIVERING, JobStatus.READY, JobStatus.EXPIRED]


def _current_jobs(db: Session, kiosk_ids: list[int]) -> dict[int, Job]:
    """compartment id -> the job that holds it (newest wins)."""
    rows = db.execute(
        select(JobCompartment.compartment_id, Job)
        .join(Job, Job.id == JobCompartment.job_id)
        .where(Job.kiosk_id.in_(kiosk_ids), Job.status.in_(_HOLDING))
        .order_by(Job.id)
    ).all()
    return {cid: job for cid, job in rows}


def overview(db: Session) -> dict:
    kiosks = list(db.scalars(select(Kiosk).order_by(Kiosk.name)))
    ids = [k.id for k in kiosks]
    holders = _current_jobs(db, ids) if ids else {}
    snaps = {s.kiosk_id: s for s in db.scalars(select(KioskSnapshot).where(KioskSnapshot.kiosk_id.in_(ids)))} if ids else {}
    pending = dict(db.execute(
        select(KioskCommand.kiosk_id, func.count())
        .where(KioskCommand.status.in_([CommandStatus.PENDING, CommandStatus.SENT]))
        .group_by(KioskCommand.kiosk_id)).all())

    out = []
    for k in kiosks:
        snap = snaps.get(k.id)
        p_rep = {p.get("id"): p for p in (snap.data.get("printers", []) if snap else [])}
        c_rep = {c.get("id"): c for c in (snap.data.get("compartments", []) if snap else [])}
        printers = []
        for p in k.printers:
            comps = []
            for c in p.compartments:
                job = holders.get(c.id)
                sensor = c_rep.get(c.id, {})
                comps.append({
                    "id": c.id, "slot_no": c.slot_no, "state": c.state.value,
                    "job_id": job.id if job else None, "job_status": job.status.value if job else None,
                    "door_closed": sensor.get("door_closed"), "paper_present": sensor.get("paper_present"),
                })
            rep = p_rep.get(p.id, {})
            printers.append({
                "id": p.id, "name": p.name, "cups_queue": p.cups_queue, "duplex": p.duplex,
                "status": p.status.value, "paper_ok": p.paper_ok, "toner_ok": p.toner_ok,
                "message": rep.get("message"), "compartments": comps,
            })
        out.append({
            "id": k.id, "name": k.name, "location": k.location, "status": effective_status(k),
            "last_seen_at": k.last_seen_at.isoformat() if k.last_seen_at else None,
            "pending_commands": pending.get(k.id, 0), "printers": printers,
        })

    counts = dict(db.execute(select(Job.status, func.count()).group_by(Job.status)).all())
    tasks = [
        {"kiosk": k["name"], "printer": p["name"], "compartment_id": c["id"], "slot_no": c["slot_no"],
         "state": c["state"], "job_id": c["job_id"]}
        for k in out for p in k["printers"] for c in p["compartments"]
        if c["state"] in (CompartmentState.EXPIRED.value, CompartmentState.FAULT.value)
    ] + [
        {"kiosk": k["name"], "printer": p["name"], "printer_id": p["id"], "state": "printer_" + p["status"],
         "message": p["message"]}
        for k in out for p in k["printers"] if p["status"] != "ok"
    ]
    return {
        "kiosks": out,
        "job_counts": {s.value if hasattr(s, "value") else s: n for s, n in counts.items()},
        "tasks": tasks,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def events(db: Session, limit: int = 100, kiosk_id: int | None = None) -> list[dict]:
    q = select(Event).order_by(Event.id.desc()).limit(min(limit, 500))
    if kiosk_id is not None:
        q = q.where(Event.kiosk_id == kiosk_id)
    return [{"id": e.id, "kiosk_id": e.kiosk_id, "job_id": e.job_id, "type": e.type, "payload": e.payload,
             "created_at": e.created_at.isoformat()} for e in db.scalars(q)]


def queue_command(db: Session, kiosk_id: int, type_: str, payload: dict, user_id: int | None) -> KioskCommand:
    if type_ not in COMMAND_TYPES:
        raise BadRequest(f"unknown command '{type_}'")
    cmd = KioskCommand(kiosk_id=kiosk_id, type=type_, payload=payload, created_by=user_id)
    db.add(cmd)
    db.flush()
    db.add(Event(kiosk_id=kiosk_id, type="command_queued", payload={"command_id": cmd.id, "type": type_, **payload}))
    db.commit()
    db.refresh(cmd)
    return cmd


def unlock_compartment(db: Session, compartment_id: int, user_id: int | None) -> KioskCommand:
    comp = db.get(Compartment, compartment_id)
    if comp is None:
        raise NotFound("compartment")
    return queue_command(db, comp.kiosk_id, "unlock", {"compartment_id": comp.id, "slot_no": comp.slot_no},
                         user_id)


def clear_printer(db: Session, printer_id: int, user_id: int | None) -> KioskCommand:
    printer = db.get(Printer, printer_id)
    if printer is None:
        raise NotFound("printer")
    return queue_command(db, printer.kiosk_id, "clear_printer", {"printer_id": printer.id}, user_id)


def list_commands(db: Session, kiosk_id: int | None = None, limit: int = 50) -> list[KioskCommand]:
    q = select(KioskCommand).order_by(KioskCommand.id.desc()).limit(limit)
    if kiosk_id is not None:
        q = q.where(KioskCommand.kiosk_id == kiosk_id)
    return list(db.scalars(q))


# ---- kiosk side

def take_commands(db: Session, kiosk: Kiosk) -> list[KioskCommand]:
    """Pending commands for this kiosk; marked as sent. Sent ones not confirmed in 2 min are resent."""
    now = datetime.now(timezone.utc)
    cmds = list(db.scalars(
        select(KioskCommand).where(KioskCommand.kiosk_id == kiosk.id,
                                   KioskCommand.status.in_([CommandStatus.PENDING, CommandStatus.SENT]))
        .order_by(KioskCommand.id)))
    out = []
    for c in cmds:
        created = c.created_at if c.created_at.tzinfo else c.created_at.replace(tzinfo=timezone.utc)
        if c.status is CommandStatus.SENT and (now - created).total_seconds() < 120:
            continue
        c.status = CommandStatus.SENT
        out.append(c)
    db.commit()
    return out


def command_done(db: Session, kiosk: Kiosk, command_id: int, ok: bool, error: str | None) -> KioskCommand:
    cmd = db.get(KioskCommand, command_id)
    if cmd is None or cmd.kiosk_id != kiosk.id:
        raise NotFound("command")
    cmd.status = CommandStatus.DONE if ok else CommandStatus.FAILED
    cmd.error = None if ok else (error or "failed")[:500]
    cmd.done_at = datetime.now(timezone.utc)
    db.add(Event(kiosk_id=kiosk.id, type="command_done",
                 payload={"command_id": cmd.id, "type": cmd.type, "ok": ok, "error": cmd.error}))
    db.commit()
    db.refresh(cmd)
    return cmd
