from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.states import CompartmentState
from ..models import Compartment, Event, Kiosk, KioskSnapshot, KioskStatus, Printer, PrinterStatus
from ..security import encrypt_str, hash_api_key, new_api_key
from .errors import NotFound


def create_kiosk(
    db: Session, name: str, location: str = "", api_key: str | None = None
) -> tuple[Kiosk, str]:
    """Create a kiosk. Returns (kiosk, api_key); the raw key is shown only once.

    `api_key` may be given for demo/dev setups (docker-compose); otherwise it is random.
    """
    api_key = api_key or new_api_key()
    kiosk = Kiosk(
        name=name,
        location=location,
        api_key_hash=hash_api_key(api_key),
        code_key_enc=encrypt_str(secrets.token_urlsafe(32)),
    )
    db.add(kiosk)
    db.flush()
    db.add(Event(kiosk_id=kiosk.id, type="kiosk_created", payload={"name": name}))
    return kiosk, api_key


def add_printer(
    db: Session,
    kiosk_id: int,
    name: str,
    cups_queue: str,
    duplex: bool = True,
    compartments: int = 5,
    capacity_sheets: int = 500,
) -> Printer:
    kiosk = db.get(Kiosk, kiosk_id)
    if kiosk is None:
        raise NotFound("kiosk")
    printer = Printer(kiosk_id=kiosk_id, name=name, cups_queue=cups_queue, duplex=duplex)
    db.add(printer)
    db.flush()
    for slot in range(1, compartments + 1):
        db.add(
            Compartment(
                kiosk_id=kiosk_id,
                printer_id=printer.id,
                slot_no=slot,
                capacity_sheets=capacity_sheets,
            )
        )
    db.flush()
    return printer


def free_compartments_by_printer(db: Session, kiosk_ids: list[int] | None = None) -> dict[int, int]:
    q = (
        select(Compartment.printer_id, func.count())
        .where(Compartment.state == CompartmentState.FREE)
        .group_by(Compartment.printer_id)
    )
    if kiosk_ids is not None:
        q = q.where(Compartment.kiosk_id.in_(kiosk_ids))
    return {pid: n for pid, n in db.execute(q).all()}


def list_kiosks(db: Session) -> list[Kiosk]:
    return list(db.scalars(select(Kiosk).order_by(Kiosk.name)))


def get_kiosk(db: Session, kiosk_id: int) -> Kiosk:
    kiosk = db.get(Kiosk, kiosk_id)
    if kiosk is None:
        raise NotFound("kiosk")
    return kiosk


def mark_compartment_emptied(db: Session, compartment_id: int) -> Compartment:
    """Operator emptied an expired compartment or cleared a fault."""
    comp = db.get(Compartment, compartment_id)
    if comp is None:
        raise NotFound("compartment")
    if comp.state not in (CompartmentState.EXPIRED, CompartmentState.FAULT):
        from .errors import Conflict

        raise Conflict(f"compartment is '{comp.state.value}', only expired or fault can be emptied")
    old = comp.state
    comp.state = CompartmentState.FREE
    db.add(
        Event(
            kiosk_id=comp.kiosk_id,
            type="compartment_emptied",
            payload={"compartment_id": comp.id, "was": old.value},
        )
    )
    return comp


ONLINE_WINDOW_SECONDS = 90


def effective_status(kiosk: Kiosk, now: datetime | None = None) -> str:
    """'online' only if the agent sent a heartbeat recently."""
    if kiosk.status is KioskStatus.MAINTENANCE:
        return kiosk.status.value
    now = now or datetime.now(timezone.utc)
    seen = kiosk.last_seen_at
    if seen is None:
        return KioskStatus.OFFLINE.value
    if seen.tzinfo is None:  # SQLite returns naive UTC
        seen = seen.replace(tzinfo=timezone.utc)
    return (KioskStatus.ONLINE if now - seen <= timedelta(seconds=ONLINE_WINDOW_SECONDS)
            else KioskStatus.OFFLINE).value


def authenticate_kiosk(db: Session, api_key: str) -> Kiosk | None:
    return db.scalar(select(Kiosk).where(Kiosk.api_key_hash == hash_api_key(api_key)))


def heartbeat(db: Session, kiosk: Kiosk, printers: list[dict], compartments: list[dict]) -> None:
    """Store what the kiosk reports about its printers and compartment sensors."""
    kiosk.last_seen_at = datetime.now(timezone.utc)
    snap = db.get(KioskSnapshot, kiosk.id)
    if snap is None:
        snap = KioskSnapshot(kiosk_id=kiosk.id)
        db.add(snap)
    snap.data = {"printers": printers, "compartments": compartments}
    snap.updated_at = kiosk.last_seen_at
    if kiosk.status is not KioskStatus.MAINTENANCE:
        kiosk.status = KioskStatus.ONLINE
    by_id = {p.id: p for p in kiosk.printers}
    for rep in printers:
        p = by_id.get(rep.get("id"))
        if p is None:
            continue
        if "status" in rep:
            p.status = PrinterStatus(rep["status"])
        for key in ("paper_ok", "toner_ok"):
            if key in rep:
                setattr(p, key, bool(rep[key]))
    # Sensor mismatch (paper seen in a compartment the server thinks is free) -> fault.
    comps = {c.id: c for c in db.scalars(select(Compartment).where(Compartment.kiosk_id == kiosk.id))}
    for rep in compartments:
        c = comps.get(rep.get("id"))
        if c is not None and rep.get("paper_present") and c.state is CompartmentState.FREE:
            c.state = CompartmentState.FAULT
            db.add(Event(kiosk_id=kiosk.id, type="compartment_fault",
                         payload={"compartment_id": c.id, "reason": "paper in a free compartment"}))
