"""Endpoints used by the kiosk agent running on each Raspberry Pi (header: X-Kiosk-Key)."""
from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import Compartment, Job, Kiosk
from ..schemas import (
    ActiveCode,
    HeartbeatIn,
    KioskCompartmentCfg,
    KioskConfigOut,
    KioskJobOut,
    KioskJobPart,
    KioskPrinterCfg,
    KioskStatusIn,
    PickupIn,
)
from ..security import decrypt_str
from ..services import jobs as job_svc
from ..services import kiosks as kiosk_svc
from .deps import current_kiosk

router = APIRouter(prefix="/kiosk", tags=["kiosk agent"])


def kiosk_job_out(job: Job) -> KioskJobOut:
    return KioskJobOut(
        id=job.id,
        printer_id=job.printer_id,
        cups_queue=job.parts[0].compartment.printer.cups_queue if job.parts else "",
        status=job.status,
        pages=job.pages,
        page_range=job.page_range,
        copies=job.copies,
        duplex=job.duplex,
        sheets=job.sheets,
        file_sha256=job.file_sha256,
        parts=[
            KioskJobPart(part_no=p.part_no, sheets=p.sheets, compartment_id=p.compartment_id,
                         slot_no=p.compartment.slot_no)
            for p in job.parts
        ],
    )


@router.get("/config", response_model=KioskConfigOut)
def config(kiosk: Kiosk = Depends(current_kiosk), db: Session = Depends(get_db)):
    comps = db.scalars(select(Compartment).where(Compartment.kiosk_id == kiosk.id)
                       .order_by(Compartment.printer_id, Compartment.slot_no))
    return KioskConfigOut(
        kiosk_id=kiosk.id,
        name=kiosk.name,
        code_key=decrypt_str(kiosk.code_key_enc),
        pickup_hours=get_settings().pickup_hours,
        printers=[KioskPrinterCfg(id=p.id, name=p.name, cups_queue=p.cups_queue, duplex=p.duplex)
                  for p in kiosk.printers],
        compartments=[KioskCompartmentCfg(id=c.id, printer_id=c.printer_id, slot_no=c.slot_no,
                                          capacity_sheets=c.capacity_sheets, state=c.state)
                      for c in comps],
    )


@router.post("/heartbeat")
def heartbeat(body: HeartbeatIn, kiosk: Kiosk = Depends(current_kiosk), db: Session = Depends(get_db)):
    kiosk_svc.heartbeat(db, kiosk, [p.model_dump(exclude_none=True) for p in body.printers],
                        [c.model_dump(exclude_none=True) for c in body.compartments])
    db.commit()
    return {"ok": True}


@router.get("/jobs/next", response_model=KioskJobOut | None)
def next_job(printer_id: int | None = None, kiosk: Kiosk = Depends(current_kiosk),
             db: Session = Depends(get_db)):
    job = job_svc.next_job(db, kiosk, printer_id)
    return kiosk_job_out(job) if job else None


@router.get("/jobs/{job_id}", response_model=KioskJobOut)
def get_job(job_id: int, kiosk: Kiosk = Depends(current_kiosk), db: Session = Depends(get_db)):
    return kiosk_job_out(job_svc.kiosk_job(db, kiosk, job_id))


@router.get("/jobs/{job_id}/file")
def job_file(job_id: int, kiosk: Kiosk = Depends(current_kiosk), db: Session = Depends(get_db)):
    data = job_svc.load_job_pdf(db, kiosk, job_id)
    return Response(content=data, media_type="application/pdf")


@router.post("/jobs/{job_id}/status", response_model=KioskJobOut)
def set_status(job_id: int, body: KioskStatusIn, kiosk: Kiosk = Depends(current_kiosk),
               db: Session = Depends(get_db)):
    return kiosk_job_out(job_svc.kiosk_transition(db, kiosk, job_id, body.status, body.error))


@router.post("/recover")
def recover(kiosk: Kiosk = Depends(current_kiosk), db: Session = Depends(get_db)):
    """Agent start-up: fail jobs that were mid-print; the agent blocks those printers."""
    jobs = job_svc.recover_interrupted(db, kiosk)
    return {"failed_jobs": [{"id": j.id, "printer_id": j.printer_id} for j in jobs]}


@router.get("/codes", response_model=list[ActiveCode])
def codes(kiosk: Kiosk = Depends(current_kiosk), db: Session = Depends(get_db)):
    return [
        ActiveCode(
            job_id=j.id,
            qr_token_hash=j.qr_token_hash,
            pin_hash=j.pin_hash,
            compartment_ids=[p.compartment_id for p in j.parts],
            slots=[p.compartment.slot_no for p in j.parts],
            expires_at=j.expires_at,
        )
        for j in job_svc.active_codes(db, kiosk)
    ]


@router.post("/pickup", response_model=KioskJobOut)
def pickup(body: PickupIn, kiosk: Kiosk = Depends(current_kiosk), db: Session = Depends(get_db)):
    return kiosk_job_out(job_svc.report_pickup(db, kiosk, body.job_id, body.method))
