from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from .config import get_settings
from .db import Base, SessionLocal, engine
from .models import Role, User
from .realtime import hub
from .security import hash_password
from .services.errors import ServiceError
from .services.jobs import expire_due_jobs

log = logging.getLogger("printlocker")


def init_db() -> None:
    # MVP: create tables directly. Alembic migrations come before the first real deployment.
    Base.metadata.create_all(engine)
    s = get_settings()
    with SessionLocal() as db:
        if db.scalar(select(User.id).where(User.role == Role.ADMIN)):
            return
        if len(s.admin_password) < 8:
            log.warning("PL_ADMIN_PASSWORD not set (or shorter than 8 characters): no admin account created")
            return
        db.add(User(email=s.admin_email.lower(), password_hash=hash_password(s.admin_password),
                    role=Role.ADMIN))
        db.commit()
        log.warning("created admin user %s", s.admin_email)


async def _expiry_loop() -> None:
    interval = get_settings().expiry_check_seconds
    while True:
        await asyncio.sleep(interval)
        try:
            with SessionLocal() as db:
                n = expire_due_jobs(db)
            if n:
                log.info("expired %d job(s)", n)
        except Exception:  # keep the loop alive
            log.exception("expiry check failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    hub.bind_loop(asyncio.get_running_loop())
    task = asyncio.create_task(_expiry_loop())
    yield
    task.cancel()
    hub.bind_loop(None)


def create_app() -> FastAPI:
    from .api import admin, auth, jobs, kiosk_agent, kiosks, ws

    app = FastAPI(title="PrintLocker API", version="0.1.0", lifespan=lifespan)
    origins = [o.strip() for o in get_settings().cors_origins.split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"],
                       allow_headers=["*"])

    @app.exception_handler(ServiceError)
    async def _service_error(_: Request, exc: ServiceError):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    @app.get("/health", tags=["meta"])
    def health():
        return {"ok": True}

    for r in (auth.router, kiosks.router, jobs.router, admin.router, kiosk_agent.router, ws.router):
        app.include_router(r, prefix="/api/v1")
    return app


app = create_app()
