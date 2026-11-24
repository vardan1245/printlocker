from __future__ import annotations

import secrets

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.states import CompartmentState
from ..models import Compartment, Event, Kiosk, Printer
from ..security import encrypt_str, hash_api_key, new_api_key
from .errors import NotFound


def create_kiosk(db: Session, name: str, location: str = "") -> tuple[Kiosk, str]:
    """Create a kiosk. Returns (kiosk, api_key); the raw key is shown only once."""
    api_key = new_api_key()
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
