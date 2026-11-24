from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import Job, User
from ..schemas import JobOut, JobPartOut, PickupOut, PreviewOut
from ..services import jobs as svc
from ..services.errors import BadRequest
from .deps import current_user

router = APIRouter(prefix="/jobs", tags=["jobs"])


def job_out(job: Job) -> JobOut:
    out = JobOut.model_validate(job, from_attributes=True)
    out.compartments = [
        JobPartOut(part_no=p.part_no, sheets=p.sheets, compartment_slot=p.compartment.slot_no)
        for p in job.parts
    ]
    return out


async def _read_pdf(file: UploadFile) -> bytes:
    limit = get_settings().max_pdf_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise BadRequest(f"file is larger than {get_settings().max_pdf_mb} MB")
    return data


@router.post("/preview", response_model=PreviewOut)
async def preview(
    printer_id: int = Form(...),
    copies: int = Form(1),
    page_range: str | None = Form(None),
    duplex: bool = Form(False),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
):
    data = await _read_pdf(file)
    p = svc.preview_job(db, printer_id, data, page_range, copies, duplex)
    return PreviewOut(
        pages=p.plan.total_pages,
        selected_pages=p.plan.selected_pages,
        sheets=p.plan.sheets,
        compartments_needed=p.plan.compartments,
        compartments_free=p.free_compartments,
        can_print=p.can_print,
    )


@router.post("", response_model=JobOut, status_code=201)
async def create(
    printer_id: int = Form(...),
    copies: int = Form(1),
    page_range: str | None = Form(None),
    duplex: bool = Form(False),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    data = await _read_pdf(file)
    job = svc.create_job(db, user, printer_id, file.filename or "document.pdf", data,
                         page_range, copies, duplex)
    return job_out(job)


@router.get("", response_model=list[JobOut])
def list_jobs(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return [job_out(j) for j in svc.list_user_jobs(db, user)]


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return job_out(svc.get_user_job(db, user, job_id))


@router.get("/{job_id}/pickup", response_model=PickupOut)
def pickup(job_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    c = svc.pickup_codes(db, user, job_id)
    return PickupOut(qr=c.qr, pin=c.pin, compartments=c.compartments, expires_at=c.expires_at)


@router.delete("/{job_id}", response_model=JobOut)
def cancel(job_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return job_out(svc.cancel_job(db, user, job_id))
