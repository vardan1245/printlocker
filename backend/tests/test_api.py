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
