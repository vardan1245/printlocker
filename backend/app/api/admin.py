from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..domain.states import JobStatus
from ..models import Compartment, Job, User
from ..schemas import (
    CommandOut,
    CompartmentOut,
    JobOut,
    KioskCreatedOut,
    KioskCreateIn,
    KioskOut,
    PrinterCreateIn,
    StatusIn,
)
from ..services import admin as admin_svc
from ..services import jobs as job_svc
from ..services import kiosks as kiosk_svc
from .deps import require_admin
from .jobs import job_out
from .kiosks import kiosk_out

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/kiosks", response_model=KioskCreatedOut, status_code=201)
def create_kiosk(body: KioskCreateIn, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    kiosk, api_key = kiosk_svc.create_kiosk(db, body.name, body.location)
    db.commit()
    db.refresh(kiosk)
    return KioskCreatedOut(kiosk=kiosk_out(kiosk, {}), api_key=api_key)


@router.post("/kiosks/{kiosk_id}/printers", response_model=KioskOut, status_code=201)
def add_printer(kiosk_id: int, body: PrinterCreateIn, db: Session = Depends(get_db),
                _: User = Depends(require_admin)):
    kiosk_svc.add_printer(db, kiosk_id, body.name, body.cups_queue, body.duplex,
                          body.compartments, body.capacity_sheets)
    db.commit()
    kiosk = kiosk_svc.get_kiosk(db, kiosk_id)
    db.refresh(kiosk)
    return kiosk_out(kiosk, kiosk_svc.free_compartments_by_printer(db, [kiosk_id]))


@router.get("/kiosks/{kiosk_id}/compartments", response_model=list[CompartmentOut])
def compartments(kiosk_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return list(db.scalars(select(Compartment).where(Compartment.kiosk_id == kiosk_id)
                           .order_by(Compartment.printer_id, Compartment.slot_no)))


@router.post("/compartments/{compartment_id}/emptied", response_model=CompartmentOut)
def compartment_emptied(compartment_id: int, db: Session = Depends(get_db),
                        _: User = Depends(require_admin)):
    comp = kiosk_svc.mark_compartment_emptied(db, compartment_id)
    db.commit()
    return comp


@router.get("/jobs", response_model=list[JobOut])
def all_jobs(status: JobStatus | None = None, limit: int = 100, db: Session = Depends(get_db),
             _: User = Depends(require_admin)):
    q = select(Job).order_by(Job.id.desc()).limit(min(limit, 500))
    if status is not None:
        q = q.where(Job.status == status)
    return [job_out(j) for j in db.scalars(q)]


@router.post("/jobs/{job_id}/status", response_model=JobOut)
def set_status(job_id: int, body: StatusIn, db: Session = Depends(get_db),
               _: User = Depends(require_admin)):
    """Move a job by hand. Until the kiosk agent exists (M2) this simulates it."""
    return job_out(job_svc.transition_job(db, job_id, body.status, body.error))


@router.post("/expire-now")
def expire_now(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return {"expired": job_svc.expire_due_jobs(db)}


@router.get("/overview")
def overview(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """Everything the panel's main screen needs in one call."""
    return admin_svc.overview(db)


@router.get("/events")
def events(limit: int = 100, kiosk_id: int | None = None, db: Session = Depends(get_db),
           _: User = Depends(require_admin)):
    return admin_svc.events(db, limit, kiosk_id)


@router.post("/compartments/{compartment_id}/unlock", response_model=CommandOut, status_code=202)
def unlock(compartment_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Ask the kiosk to open this compartment's door (e.g. to empty an expired one)."""
    return admin_svc.unlock_compartment(db, compartment_id, user.id)


@router.post("/printers/{printer_id}/clear", response_model=CommandOut, status_code=202)
def clear_printer(printer_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """After removing loose paper: tell the kiosk the printer column may take jobs again."""
    return admin_svc.clear_printer(db, printer_id, user.id)


@router.get("/commands", response_model=list[CommandOut])
def commands(kiosk_id: int | None = None, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return admin_svc.list_commands(db, kiosk_id)
