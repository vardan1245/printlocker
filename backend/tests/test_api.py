"""End-to-end API tests on SQLite. Skipped if the web dependencies aren't installed."""
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

for mod in ("fastapi", "sqlalchemy", "argon2", "httpx", "email_validator", "pydantic_settings"):
    pytest.importorskip(mod)

_tmp = tempfile.mkdtemp(prefix="pl-test-")
os.environ["PL_DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["PL_STORAGE_DIR"] = f"{_tmp}/pdfs"
os.environ["PL_ADMIN_EMAIL"] = "admin@test.local"
os.environ["PL_ADMIN_PASSWORD"] = "admin12345"
os.environ["PL_EXPIRY_CHECK_SECONDS"] = "3600"
from cryptography.fernet import Fernet  # noqa: E402

os.environ["PL_FILE_KEY"] = Fernet.generate_key().decode()

from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Job  # noqa: E402
from app.services.jobs import expire_due_jobs  # noqa: E402

from .conftest import make_pdf  # noqa: E402

API = "/api/v1"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def login(client, email, password):
    r = client.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="module")
def admin(client):
    return login(client, "admin@test.local", "admin12345")


@pytest.fixture(scope="module")
def user(client):
    r = client.post(f"{API}/auth/register", json={"email": "u1@test.local", "password": "password1"})
    assert r.status_code == 201, r.text
    return login(client, "u1@test.local", "password1")


@pytest.fixture(scope="module")
def printer_ids(client, admin):
    r = client.post(f"{API}/admin/kiosks", json={"name": "K1", "location": "Lobby"}, headers=admin)
    assert r.status_code == 201, r.text
    assert r.json()["api_key"].startswith("plk_")
    kid = r.json()["kiosk"]["id"]
    for name in ("A", "B"):
        r = client.post(f"{API}/admin/kiosks/{kid}/printers",
                        json={"name": name, "cups_queue": name.lower(), "compartments": 3},
                        headers=admin)
        assert r.status_code == 201, r.text
    return [p["id"] for p in r.json()["printers"]]


def upload(client, headers, printer_id, pages=3, **form):
    data = {"printer_id": str(printer_id), **{k: str(v) for k, v in form.items()}}
    return client.post(f"{API}/jobs", data=data, headers=headers,
                       files={"file": ("doc.pdf", make_pdf(pages), "application/pdf")})


def advance(client, admin, job_id, *states):
    for s in states:
        r = client.post(f"{API}/admin/jobs/{job_id}/status", json={"status": s}, headers=admin)
        assert r.status_code == 200, r.text
    return r.json()


def test_auth(client, user):
    assert client.get(f"{API}/auth/me").status_code == 401
    assert client.get(f"{API}/auth/me", headers=user).json()["email"] == "u1@test.local"
    r = client.post(f"{API}/auth/register", json={"email": "U1@test.local", "password": "password1"})
    assert r.status_code == 409
    r = client.post(f"{API}/auth/login", json={"email": "u1@test.local", "password": "wrong-pass"})
    assert r.status_code == 401


def test_user_cannot_use_admin(client, user):
    assert client.post(f"{API}/admin/kiosks", json={"name": "x"}, headers=user).status_code == 403


def test_kiosk_list(client, user, printer_ids):
    kiosks = client.get(f"{API}/kiosks", headers=user).json()
    printers = kiosks[0]["printers"]
    assert [p["compartments_free"] for p in printers] == [3, 3]


def test_preview(client, user, printer_ids):
    r = client.post(f"{API}/jobs/preview", headers=user,
                    data={"printer_id": str(printer_ids[0]), "copies": "2", "duplex": "true"},
                    files={"file": ("d.pdf", make_pdf(5), "application/pdf")})
    assert r.status_code == 200, r.text
    assert r.json()["sheets"] == 6 and r.json()["can_print"]


def test_full_flow_and_codes(client, user, admin, printer_ids):
    r = upload(client, user, printer_ids[0], pages=3, page_range="1-2", copies=2)
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "queued" and job["sheets"] == 4
    assert job["compartments"][0]["compartment_slot"] == 1

    assert client.get(f"{API}/jobs/{job['id']}/pickup", headers=user).status_code == 409
    advance(client, admin, job["id"], "printing", "delivering", "ready")
    codes = client.get(f"{API}/jobs/{job['id']}/pickup", headers=user).json()
    assert codes["qr"].startswith(f"PL1:{job['id']}:") and len(codes["pin"]) == 6
    assert codes["compartments"] == [1]

    with SessionLocal() as db:  # PDF deleted once printing finished
        assert db.get(Job, job["id"]).file_path is None

    done = advance(client, admin, job["id"], "collected")
    assert done["status"] == "collected"
    printers = client.get(f"{API}/kiosks", headers=user).json()[0]["printers"]
    assert printers[0]["compartments_free"] == 3


def test_illegal_transition_is_409(client, user, admin, printer_ids):
    job = upload(client, user, printer_ids[0]).json()
    r = client.post(f"{API}/admin/jobs/{job['id']}/status", json={"status": "ready"}, headers=admin)
    assert r.status_code == 409
    assert client.delete(f"{API}/jobs/{job['id']}", headers=user).json()["status"] == "cancelled"


def test_split_job_and_full_column(client, user, admin, printer_ids):
    pid = printer_ids[1]
    r = upload(client, user, pid, pages=700)  # 700 sheets -> 2 compartments
    assert r.status_code == 201, r.text
    assert [p["sheets"] for p in r.json()["compartments"]] == [350, 350]
    assert upload(client, user, pid, pages=1).status_code == 201  # last free one
    r = upload(client, user, pid, pages=1)
    assert r.status_code == 409 and "free" in r.json()["detail"]


def test_bad_uploads(client, user, printer_ids):
    pid = printer_ids[0]
    r = client.post(f"{API}/jobs", headers=user, data={"printer_id": str(pid)},
                    files={"file": ("x.pdf", b"not a pdf", "application/pdf")})
    assert r.status_code == 422
    assert upload(client, user, pid, pages=3, page_range="2-9").status_code == 422
    assert upload(client, user, pid, pages=3, copies=0).status_code == 422
    assert upload(client, user, 99999).status_code == 404


def test_other_user_cannot_see_job(client, user, printer_ids):
    job = upload(client, user, printer_ids[0]).json()
    client.post(f"{API}/auth/register", json={"email": "u2@test.local", "password": "password2"})
    other = login(client, "u2@test.local", "password2")
    assert client.get(f"{API}/jobs/{job['id']}", headers=other).status_code == 404
    client.delete(f"{API}/jobs/{job['id']}", headers=user)


def test_expiry_and_emptying(client, user, admin, printer_ids):
    job = upload(client, user, printer_ids[0]).json()
    advance(client, admin, job["id"], "printing", "delivering", "ready")
    with SessionLocal() as db:
        n = expire_due_jobs(db, now=datetime.now(timezone.utc) + timedelta(hours=25))
    assert n == 1
    assert client.get(f"{API}/jobs/{job['id']}", headers=user).json()["status"] == "expired"
    assert client.get(f"{API}/jobs/{job['id']}/pickup", headers=user).status_code == 409

    kid = client.get(f"{API}/kiosks", headers=user).json()[0]["id"]
    comps = client.get(f"{API}/admin/kiosks/{kid}/compartments", headers=admin).json()
    expired = [c for c in comps if c["state"] == "expired"]
    assert len(expired) == 1
    r = client.post(f"{API}/admin/compartments/{expired[0]['id']}/emptied", headers=admin)
    assert r.json()["state"] == "free"


# ---------------------------------------------------------------- kiosk agent endpoints (M2)

@pytest.fixture(scope="module")
def kiosk(client, admin):
    r = client.post(f"{API}/admin/kiosks", json={"name": "K2"}, headers=admin)
    key = r.json()["api_key"]
    kid = r.json()["kiosk"]["id"]
    r = client.post(f"{API}/admin/kiosks/{kid}/printers",
                    json={"name": "P", "cups_queue": "p", "compartments": 3}, headers=admin)
    return {"id": kid, "printer_id": r.json()["printers"][0]["id"], "h": {"X-Kiosk-Key": key}}


def test_kiosk_auth(client, kiosk):
    assert client.get(f"{API}/kiosk/config").status_code == 401
    assert client.get(f"{API}/kiosk/config", headers={"X-Kiosk-Key": "nope"}).status_code == 401
    cfg = client.get(f"{API}/kiosk/config", headers=kiosk["h"]).json()
    assert cfg["kiosk_id"] == kiosk["id"] and len(cfg["compartments"]) == 3 and cfg["code_key"]


def test_heartbeat_marks_online_and_printer_error(client, user, kiosk):
    def status():
        ks = client.get(f"{API}/kiosks/{kiosk['id']}", headers=user).json()
        return ks["status"], ks["printers"][0]["status"]

    assert status()[0] == "offline"
    client.post(f"{API}/kiosk/heartbeat", headers=kiosk["h"],
                json={"printers": [{"id": kiosk["printer_id"], "status": "error"}]})
    assert status() == ("online", "error")
    assert upload(client, user, kiosk["printer_id"]).status_code == 409  # printer down
    client.post(f"{API}/kiosk/heartbeat", headers=kiosk["h"],
                json={"printers": [{"id": kiosk["printer_id"], "status": "ok"}]})
    assert status() == ("online", "ok")


def test_agent_flow(client, user, kiosk):
    import hashlib

    from app.domain.codes import hash_code, parse_qr

    job = upload(client, user, kiosk["printer_id"], pages=4).json()
    nxt = client.get(f"{API}/kiosk/jobs/next", params={"printer_id": kiosk["printer_id"]},
                     headers=kiosk["h"]).json()
    assert nxt["id"] == job["id"] and nxt["parts"][0]["slot_no"] == 1 and nxt["cups_queue"] == "p"

    for s in ("printing",):
        assert client.post(f"{API}/kiosk/jobs/{job['id']}/status", json={"status": s},
                           headers=kiosk["h"]).status_code == 200
    pdf = client.get(f"{API}/kiosk/jobs/{job['id']}/file", headers=kiosk["h"]).content
    assert hashlib.sha256(pdf).hexdigest() == nxt["file_sha256"]
    for s in ("delivering", "ready", "ready"):  # repeated report is accepted (retry)
        assert client.post(f"{API}/kiosk/jobs/{job['id']}/status", json={"status": s},
                           headers=kiosk["h"]).status_code == 200
    # file gone after printing
    assert client.get(f"{API}/kiosk/jobs/{job['id']}/file", headers=kiosk["h"]).status_code == 409
    # kiosk can't mark collected via status
    assert client.post(f"{API}/kiosk/jobs/{job['id']}/status", json={"status": "collected"},
                       headers=kiosk["h"]).status_code == 409

    codes = client.get(f"{API}/kiosk/codes", headers=kiosk["h"]).json()
    mine = next(c for c in codes if c["job_id"] == job["id"])
    shown = client.get(f"{API}/jobs/{job['id']}/pickup", headers=user).json()
    key = client.get(f"{API}/kiosk/config", headers=kiosk["h"]).json()["code_key"]
    assert hash_code(key, shown["pin"]) == mine["pin_hash"]
    assert hash_code(key, parse_qr(shown["qr"]).token) == mine["qr_token_hash"]

    r = client.post(f"{API}/kiosk/pickup", json={"job_id": job["id"], "method": "qr"}, headers=kiosk["h"])
    assert r.json()["status"] == "collected"
    r = client.post(f"{API}/kiosk/pickup", json={"job_id": job["id"], "method": "qr"}, headers=kiosk["h"])
    assert r.status_code == 200  # idempotent


def test_kiosk_cannot_touch_other_kiosks_jobs(client, user, kiosk, printer_ids):
    other = upload(client, user, printer_ids[0]).json()
    r = client.post(f"{API}/kiosk/jobs/{other['id']}/status", json={"status": "printing"}, headers=kiosk["h"])
    assert r.status_code == 404
    client.delete(f"{API}/jobs/{other['id']}", headers=user)


def test_websocket_live_status(client, user, admin, kiosk):
    token = user["Authorization"].split()[1]
    with client.websocket_connect(f"{API}/ws/user?token={token}") as ws:
        assert ws.receive_json()["type"] == "hello"
        job = upload(client, user, kiosk["printer_id"]).json()
        msg = ws.receive_json()
        assert msg["job_id"] == job["id"] and msg["status"] == "queued"
        client.post(f"{API}/kiosk/jobs/{job['id']}/status", json={"status": "printing"}, headers=kiosk["h"])
        assert ws.receive_json()["status"] == "printing"
        client.post(f"{API}/admin/jobs/{job['id']}/status", json={"status": "failed"}, headers=admin)
        assert ws.receive_json()["status"] == "failed"


def test_websocket_rejects_bad_token(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"{API}/ws/user?token=bad") as ws:
            ws.receive_json()


def test_test_page(client):
    r = client.get("/")
    assert r.status_code == 200 and "PrintLocker" in r.text


def test_recover_and_late_pickup(client, user, admin, kiosk):
    job = upload(client, user, kiosk["printer_id"]).json()
    client.post(f"{API}/kiosk/jobs/{job['id']}/status", json={"status": "printing"}, headers=kiosk["h"])
    r = client.post(f"{API}/kiosk/recover", headers=kiosk["h"]).json()
    assert {"id": job["id"], "printer_id": kiosk["printer_id"]} in r["failed_jobs"]
    assert client.get(f"{API}/jobs/{job['id']}", headers=user).json()["status"] == "failed"

    job2 = upload(client, user, kiosk["printer_id"]).json()
    for s in ("printing", "delivering", "ready"):
        client.post(f"{API}/kiosk/jobs/{job2['id']}/status", json={"status": s}, headers=kiosk["h"])
    with SessionLocal() as db:
        expire_due_jobs(db, now=datetime.now(timezone.utc) + timedelta(hours=25))
    r = client.post(f"{API}/kiosk/pickup", json={"job_id": job2["id"], "method": "pin"}, headers=kiosk["h"])
    assert r.status_code == 200 and r.json()["status"] == "collected"
