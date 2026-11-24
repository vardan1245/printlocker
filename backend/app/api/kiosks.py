from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Kiosk, User
from ..schemas import KioskOut, PrinterOut
from ..services import kiosks as svc
from .deps import current_user

router = APIRouter(prefix="/kiosks", tags=["kiosks"])


def kiosk_out(kiosk: Kiosk, free: dict[int, int]) -> KioskOut:
    return KioskOut(
        id=kiosk.id,
        name=kiosk.name,
        location=kiosk.location,
        status=kiosk.status.value,
        last_seen_at=kiosk.last_seen_at,
        printers=[
            PrinterOut(
                id=p.id,
                name=p.name,
                duplex=p.duplex,
                status=p.status.value,
                paper_ok=p.paper_ok,
                toner_ok=p.toner_ok,
                compartments_total=len(p.compartments),
                compartments_free=free.get(p.id, 0),
            )
            for p in kiosk.printers
        ],
    )


@router.get("", response_model=list[KioskOut])
def list_kiosks(db: Session = Depends(get_db), _: User = Depends(current_user)):
    kiosks = svc.list_kiosks(db)
    free = svc.free_compartments_by_printer(db)
    return [kiosk_out(k, free) for k in kiosks]


@router.get("/{kiosk_id}", response_model=KioskOut)
def get_kiosk(kiosk_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    kiosk = svc.get_kiosk(db, kiosk_id)
    return kiosk_out(kiosk, svc.free_compartments_by_printer(db, [kiosk.id]))
