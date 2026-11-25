from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .domain.states import CompartmentState, JobStatus


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _enum(e):
    return SAEnum(e, values_callable=lambda x: [m.value for m in x], native_enum=False, length=20)


class Role(str, Enum):
    USER = "user"
    OPERATOR = "operator"
    ADMIN = "admin"


class KioskStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    MAINTENANCE = "maintenance"


class PrinterStatus(str, Enum):
    OK = "ok"
    ERROR = "error"
    OFFLINE = "offline"


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(_enum(Role), default=Role.USER)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Kiosk(Base):
    __tablename__ = "kiosks"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    location: Mapped[str] = mapped_column(String(255), default="")
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # Key the kiosk uses to hash/check pickup codes offline. Encrypted at rest.
    code_key_enc: Mapped[str] = mapped_column(String(255))
    status: Mapped[KioskStatus] = mapped_column(_enum(KioskStatus), default=KioskStatus.OFFLINE)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    printers: Mapped[list[Printer]] = relationship(back_populates="kiosk", order_by="Printer.id")


class Printer(Base):
    __tablename__ = "printers"
    id: Mapped[int] = mapped_column(primary_key=True)
    kiosk_id: Mapped[int] = mapped_column(ForeignKey("kiosks.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    cups_queue: Mapped[str] = mapped_column(String(100))
    duplex: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[PrinterStatus] = mapped_column(_enum(PrinterStatus), default=PrinterStatus.OK)
    paper_ok: Mapped[bool] = mapped_column(Boolean, default=True)
    toner_ok: Mapped[bool] = mapped_column(Boolean, default=True)

    kiosk: Mapped[Kiosk] = relationship(back_populates="printers")
    compartments: Mapped[list[Compartment]] = relationship(
        back_populates="printer", order_by="Compartment.slot_no"
    )


class Compartment(Base):
    __tablename__ = "compartments"
    __table_args__ = (UniqueConstraint("printer_id", "slot_no"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    kiosk_id: Mapped[int] = mapped_column(ForeignKey("kiosks.id"), index=True)
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id"), index=True)
    slot_no: Mapped[int] = mapped_column(Integer)
    capacity_sheets: Mapped[int] = mapped_column(Integer, default=500)
    state: Mapped[CompartmentState] = mapped_column(
        _enum(CompartmentState), default=CompartmentState.FREE, index=True
    )

    printer: Mapped[Printer] = relationship(back_populates="compartments")


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id"), index=True)
    kiosk_id: Mapped[int] = mapped_column(ForeignKey("kiosks.id"), index=True)

    original_filename: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str | None] = mapped_column(String(255), nullable=True)  # None once deleted
    file_sha256: Mapped[str] = mapped_column(String(64))
    pages: Mapped[int] = mapped_column(Integer)
    page_range: Mapped[str | None] = mapped_column(String(255), nullable=True)
    selected_pages: Mapped[int] = mapped_column(Integer)
    copies: Mapped[int] = mapped_column(Integer, default=1)
    duplex: Mapped[bool] = mapped_column(Boolean, default=False)
    sheets: Mapped[int] = mapped_column(Integer)
    price_cents: Mapped[int] = mapped_column(Integer, default=0)  # phase 2

    status: Mapped[JobStatus] = mapped_column(_enum(JobStatus), default=JobStatus.UPLOADED, index=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Pickup codes: encrypted copies for showing to the user, HMAC hashes for the kiosk.
    pin_enc: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pin_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    qr_token_enc: Mapped[str | None] = mapped_column(String(255), nullable=True)
    qr_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    printing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivering_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    parts: Mapped[list[JobCompartment]] = relationship(
        back_populates="job", order_by="JobCompartment.part_no", cascade="all, delete-orphan"
    )


class JobCompartment(Base):
    __tablename__ = "job_compartments"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    compartment_id: Mapped[int] = mapped_column(ForeignKey("compartments.id"), index=True)
    part_no: Mapped[int] = mapped_column(Integer)
    sheets: Mapped[int] = mapped_column(Integer)

    job: Mapped[Job] = relationship(back_populates="parts")
    compartment: Mapped[Compartment] = relationship()


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    kiosk_id: Mapped[int | None] = mapped_column(ForeignKey("kiosks.id"), nullable=True, index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), nullable=True, index=True)
    type: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CommandStatus(str, Enum):
    PENDING = "pending"  # waiting for the kiosk to pick it up
    SENT = "sent"  # kiosk has it
    DONE = "done"
    FAILED = "failed"


class KioskCommand(Base):
    """An operator action the kiosk must carry out (open a door, clear a printer)."""

    __tablename__ = "kiosk_commands"
    id: Mapped[int] = mapped_column(primary_key=True)
    kiosk_id: Mapped[int] = mapped_column(ForeignKey("kiosks.id"), index=True)
    type: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[CommandStatus] = mapped_column(_enum(CommandStatus), default=CommandStatus.PENDING, index=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KioskSnapshot(Base):
    """Last heartbeat as the kiosk sent it: printer states (with reasons) and door/paper sensors."""

    __tablename__ = "kiosk_snapshots"
    kiosk_id: Mapped[int] = mapped_column(ForeignKey("kiosks.id"), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StoredFile(Base):
    """Encrypted uploaded PDF (DbFileStore). Deleted when its job reaches a final state."""
    __tablename__ = "stored_files"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    size: Mapped[int] = mapped_column(Integer)  # plain size in bytes
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
