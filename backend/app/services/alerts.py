"""Notifications: email to users about their jobs, alerts to operators about the kiosks.

Channels (each switched on by its settings; with none configured, messages are only logged):
- email via an HTTP email API (Resend). HTTP rather than SMTP because hosts like Render's free tier
  block the SMTP ports. PL_RESEND_API_KEY, PL_EMAIL_FROM.
- Telegram for operators: PL_TELEGRAM_BOT_TOKEN, PL_TELEGRAM_CHAT_ID (a group with the operators).
- operator email: PL_OPERATOR_EMAILS (comma-separated), uses the email channel.

Every alert has a key ("job:12:ready", "printer:3:error"); it is sent once and re-armed only when the
condition clears (printer back to ok, kiosk back online), so a flapping sensor doesn't spam anyone.
Sending happens on a background thread: a slow mail API never delays a request or the kiosk.
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..config import get_settings
from ..db import SessionLocal
from ..models import AlertSent, Job, Kiosk, User
from ..domain.states import JobStatus

log = logging.getLogger("printlocker.alerts")

# Replaced in tests to capture messages instead of sending them.
transport = None
_RUN_INLINE = False  # tests: send synchronously


# ------------------------------------------------------------------ dedup
def first_time(key: str) -> bool:
    """True the first time `key` is seen; False until rearm(key)."""
    with SessionLocal() as db:
        db.add(AlertSent(key=key))
        try:
            db.commit()
            return True
        except IntegrityError:
            db.rollback()
            return False


def rearm(key: str) -> None:
    with SessionLocal() as db:
        row = db.get(AlertSent, key)
        if row is not None:
            db.delete(row)
            db.commit()


# ------------------------------------------------------------------ channels
def _post_json(url: str, body: dict, headers: dict | None = None) -> None:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()


def _deliver(kind: str, to: str | None, subject: str, text: str) -> None:
    if transport is not None:
        transport(kind, to, subject, text)
        return
    s = get_settings()
    try:
        if kind == "email" and s.resend_api_key and to:
            _post_json("https://api.resend.com/emails",
                       {"from": s.email_from, "to": [to], "subject": subject, "text": text},
                       {"Authorization": f"Bearer {s.resend_api_key}"})
        elif kind == "telegram" and s.telegram_bot_token and s.telegram_chat_id:
            _post_json(f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage",
                       {"chat_id": s.telegram_chat_id, "text": f"{subject}\n{text}"})
        else:
            log.info("[%s not configured] %s -> %s: %s", kind, to or "-", subject, text.replace("\n", " | "))
            return
        log.info("sent %s to %s: %s", kind, to or s.telegram_chat_id, subject)
    except Exception as exc:  # never let a notification failure break anything else
        log.warning("could not send %s (%s): %s", kind, subject, exc)


def _send(kind: str, to: str | None, subject: str, text: str) -> None:
    if _RUN_INLINE:
        _deliver(kind, to, subject, text)
    else:
        threading.Thread(target=_deliver, args=(kind, to, subject, text), daemon=True).start()


def to_user(email: str, subject: str, text: str) -> None:
    _send("email", email, subject, text)


def to_operators(subject: str, text: str) -> None:
    _send("telegram", None, subject, text)
    for addr in [a.strip() for a in get_settings().operator_emails.split(",") if a.strip()]:
        _send("email", addr, subject, text)


# ------------------------------------------------------------------ helpers
def _local(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    tz = ZoneInfo(get_settings().timezone)
    return dt.astimezone(tz).strftime("%d %b %H:%M")


def _footer() -> str:
    url = get_settings().app_url
    return f"\n\nOpen the PrintLocker app{': ' + url if url else ''}"


# ------------------------------------------------------------------ user notifications
def job_changed(info: dict) -> None:
    """Called after a job's status changed (see jobs.notify). `info` is plain data, no ORM objects."""
    status = info["status"]
    if status not in ("ready", "failed", "expired"):
        return
    if not first_time(f"job:{info['job_id']}:{status}"):
        return
    with SessionLocal() as db:
        job = db.get(Job, info["job_id"])
        user = db.get(User, job.user_id) if job else None
        kiosk = db.get(Kiosk, job.kiosk_id) if job else None
        if not (job and user and kiosk):
            return
        name, place, comps = job.original_filename, f"{kiosk.name} ({kiosk.location})", info["compartments"]
        email, expires = user.email, job.expires_at
    where = "compartment " + ", ".join(f"#{c}" for c in comps) if comps else ""
    if status == "ready":
        to_user(email, f"Your print is ready: {name}",
                f"'{name}' is ready at {place}, {where}.\n"
                f"Collect it by {_local(expires)}. Show the QR code from the app at the kiosk, or type the PIN."
                + _footer())
    elif status == "failed":
        to_user(email, f"Print failed: {name}",
                f"'{name}' could not be printed at {place}: {info.get('error') or 'unknown error'}.\n"
                "Nothing was charged. Please send it again." + _footer())
    elif status == "expired":
        to_user(email, f"Print not collected: {name}",
                f"'{name}' was not collected in time and has been removed from {where} at {place}. "
                "Please send it again if you still need it." + _footer())
        to_operators(f"Empty {where} at {kiosk_name(info)}",
                     f"Job {info['job_id']} ('{name}') expired. Take the paper to the secure bin, then press "
                     "'Mark emptied' in the admin panel.")
    if status == "failed":
        to_operators(f"Job {info['job_id']} failed at {kiosk_name(info)}",
                     f"{info.get('error') or 'unknown error'}. The printer is blocked until an operator clears it.")


def kiosk_name(info: dict) -> str:
    with SessionLocal() as db:
        job = db.get(Job, info["job_id"])
        k = db.get(Kiosk, job.kiosk_id) if job else None
        return k.name if k else "kiosk"


def remind_due(now: datetime | None = None) -> int:
    """Remind users whose prints expire soon. Called from the background loop."""
    now = now or datetime.now(timezone.utc)
    soon = now + timedelta(minutes=get_settings().reminder_minutes)
    n = 0
    with SessionLocal() as db:
        rows = db.execute(select(Job.id, Job.original_filename, Job.expires_at, User.email, Kiosk.name)
                          .join(User, User.id == Job.user_id).join(Kiosk, Kiosk.id == Job.kiosk_id)
                          .where(Job.status == JobStatus.READY, Job.expires_at <= soon)).all()
    for job_id, name, expires, email, kname in rows:
        if first_time(f"job:{job_id}:reminder"):
            to_user(email, f"Reminder: collect your print by {_local(expires)}",
                    f"'{name}' is waiting at {kname}. After {_local(expires)} it is removed." + _footer())
            n += 1
    return n


# ------------------------------------------------------------------ operator alerts
def printer_report(kiosk_name_: str, printer_id: int, printer_name: str, status: str, paper_ok: bool,
                   toner_ok: bool, message: str | None) -> None:
    """Called from the heartbeat with each printer's current state."""
    checks = [("error", status != "ok", f"Printer {printer_name} at {kiosk_name_}: {status}",
               message or "Check the printer, then clear it in the admin panel."),
              ("paper", not paper_ok, f"Out of paper: {printer_name} at {kiosk_name_}", "Refill the paper cassette."),
              ("toner", not toner_ok, f"Toner low: {printer_name} at {kiosk_name_}", "Replace the toner cartridge.")]
    for kind, bad, subject, text in checks:
        key = f"printer:{printer_id}:{kind}"
        if bad:
            if first_time(key):
                to_operators(subject, text)
        else:
            rearm(key)


def kiosk_online(kiosk_id: int) -> None:
    rearm(f"kiosk:{kiosk_id}:offline")


def check_offline(now: datetime | None = None) -> int:
    from .kiosks import effective_status

    n = 0
    with SessionLocal() as db:
        kiosks = [(k.id, k.name, k.last_seen_at, effective_status(k, now)) for k in db.scalars(select(Kiosk))]
    for kid, name, seen, status in kiosks:
        if status == "offline" and seen is not None and first_time(f"kiosk:{kid}:offline"):
            to_operators(f"Kiosk offline: {name}",
                         f"No heartbeat since {_local(seen)}. Check its power and network. Prints that are "
                         "ready can still be collected with the PIN or QR (the kiosk works offline).")
            n += 1
    return n
