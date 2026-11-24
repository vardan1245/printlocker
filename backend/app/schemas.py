from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from .domain.states import CompartmentState, JobStatus
from .models import Role


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- auth ----

class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class TokensOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserOut(ORM):
    id: int
    email: str
    role: Role
    created_at: datetime


# ---- kiosks ----

class PrinterOut(ORM):
    id: int
    name: str
    duplex: bool
    status: str
    paper_ok: bool
    toner_ok: bool
    compartments_total: int = 0
    compartments_free: int = 0


class KioskOut(ORM):
    id: int
    name: str
    location: str
    status: str
    last_seen_at: datetime | None
    printers: list[PrinterOut] = []


class KioskCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    location: str = ""


class KioskCreatedOut(BaseModel):
    kiosk: KioskOut
    api_key: str  # shown once


class PrinterCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    cups_queue: str = Field(min_length=1, max_length=100)
    duplex: bool = True
    compartments: int = Field(default=5, ge=1, le=30)
    capacity_sheets: int = Field(default=500, ge=50, le=2000)


class CompartmentOut(ORM):
    id: int
    printer_id: int
    slot_no: int
    capacity_sheets: int
    state: CompartmentState


# ---- jobs ----

class JobPartOut(BaseModel):
    part_no: int
    sheets: int
    compartment_slot: int


class JobOut(ORM):
    id: int
    kiosk_id: int
    printer_id: int
    original_filename: str
    pages: int
    page_range: str | None
    selected_pages: int
    copies: int
    duplex: bool
    sheets: int
    status: JobStatus
    error: str | None
    created_at: datetime
    ready_at: datetime | None
    expires_at: datetime | None
    collected_at: datetime | None
    # Named differently from Job.parts so from_attributes doesn't try to read ORM rows here.
    compartments: list[JobPartOut] = []


class PreviewOut(BaseModel):
    pages: int
    selected_pages: int
    sheets: int
    compartments_needed: int
    compartments_free: int
    can_print: bool


class PickupOut(BaseModel):
    qr: str
    pin: str
    compartments: list[int]
    expires_at: datetime


class StatusIn(BaseModel):
    status: JobStatus
    error: str | None = None


# ---- kiosk agent ----

class KioskCompartmentCfg(BaseModel):
    id: int
    printer_id: int
    slot_no: int
    capacity_sheets: int
    state: CompartmentState


class KioskPrinterCfg(BaseModel):
    id: int
    name: str
    cups_queue: str
    duplex: bool


class KioskConfigOut(BaseModel):
    kiosk_id: int
    name: str
    code_key: str  # HMAC key for checking pickup codes offline
    pickup_hours: int
    printers: list[KioskPrinterCfg]
    compartments: list[KioskCompartmentCfg]


class PrinterReport(BaseModel):
    id: int
    status: str | None = None
    paper_ok: bool | None = None
    toner_ok: bool | None = None


class CompartmentReport(BaseModel):
    id: int
    door_closed: bool | None = None
    paper_present: bool | None = None


class HeartbeatIn(BaseModel):
    printers: list[PrinterReport] = []
    compartments: list[CompartmentReport] = []


class KioskJobPart(BaseModel):
    part_no: int
    sheets: int
    compartment_id: int
    slot_no: int


class KioskJobOut(BaseModel):
    id: int
    printer_id: int
    cups_queue: str
    status: JobStatus
    pages: int
    page_range: str | None
    copies: int
    duplex: bool
    sheets: int
    file_sha256: str
    parts: list[KioskJobPart]


class KioskStatusIn(BaseModel):
    status: JobStatus
    error: str | None = None


class ActiveCode(BaseModel):
    job_id: int
    qr_token_hash: str
    pin_hash: str
    compartment_ids: list[int]
    slots: list[int]
    expires_at: datetime


class PickupIn(BaseModel):
    job_id: int
    method: str = Field(pattern="^(qr|pin|admin)$")
