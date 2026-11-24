# PrintLocker

Self-service A4 print kiosk with QR-unlocked pickup compartments.
This repository currently contains **milestone M1: the backend**.

```
printlocker/
├── docker-compose.yml      backend + PostgreSQL
└── backend/
    ├── app/
    │   ├── domain/         pure business rules (no web/DB imports) — reused by the kiosk agent later
    │   │   ├── states.py       job state machine + compartment state per job state
    │   │   ├── sheets.py       page ranges, sheet count, splitting big jobs across compartments
    │   │   ├── reservation.py  picking free compartments
    │   │   ├── pdf.py          PDF validation + page count
    │   │   ├── codes.py        QR tokens, 6-digit PINs, per-kiosk HMAC hashes
    │   │   └── storage.py      encrypted PDF storage on disk
    │   ├── services/       use cases on top of the database (create job, transitions, expiry, kiosks)
    │   ├── api/            FastAPI routes: auth, kiosks, jobs, admin
    │   ├── models.py       SQLAlchemy tables
    │   ├── schemas.py      request/response models
    │   └── main.py         app, startup, expiry background loop
    ├── scripts/seed.py     demo kiosk (2 printers × 5 compartments) + demo user
    └── tests/              domain tests + end-to-end API tests
```

## Run with Docker

```bash
cd backend
cp .env.example .env
# put a key into PL_FILE_KEY:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
cd ..
docker compose up --build
docker compose exec backend python -m scripts.seed   # demo kiosk + demo user
```

Open http://localhost:8000/docs for the interactive API.

## Run locally without Docker (SQLite)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env    # set PL_FILE_KEY and PL_JWT_SECRET
python -m scripts.seed
uvicorn app.main:app --reload
```

## Tests

```bash
cd backend
pytest -q
```

`tests/test_domain.py` covers the rules (state machine, page ranges, sheet counts, split jobs,
reservation, PDF checks, codes, encryption). `tests/test_api.py` runs the whole flow through
the HTTP API on SQLite: register/login, kiosk list, preview, upload, status changes, pickup codes,
cancel, split job, full column, bad uploads, access control, expiry and emptying a compartment.

## Try the flow by hand (until the kiosk agent exists)

1. `POST /api/v1/auth/login` as `demo@example.com` / `demo12345`, copy the access token.
2. `GET /api/v1/kiosks` → pick a printer id.
3. `POST /api/v1/jobs` (multipart: `file`, `printer_id`, optional `copies`, `page_range`, `duplex`) → status `queued`.
4. As admin, `POST /api/v1/admin/jobs/{id}/status` with `printing`, then `delivering`, then `ready`
   (this is what the kiosk agent will do in M2).
5. `GET /api/v1/jobs/{id}/pickup` → QR text + PIN + compartment number.
6. Admin sets `collected` → compartment is free again.

## Rules implemented

| Rule | Where |
| --- | --- |
| Job states: uploaded → queued → printing → delivering → ready → collected; side exits failed / expired / cancelled | `domain/states.py` |
| User can cancel only while uploaded/queued | `domain/states.py` |
| Sheets = selected pages × copies; duplex halves per copy (each copy starts on a new sheet) | `domain/sheets.py` |
| A compartment holds 500 sheets; bigger jobs are split into balanced parts (700 → 350 + 350) | `domain/sheets.py` |
| Job is accepted only if the printer's column has enough free compartments; lowest slots first | `domain/reservation.py`, `services/jobs.py` |
| Compartments: reserved at queue, occupied at ready, freed at collect/cancel/failed; fault if delivery fails; expired after 24 h until an operator empties it | `domain/states.py` |
| PDF stored encrypted; deleted once printing finishes or the job ends | `domain/storage.py`, `services/jobs.py` |
| PIN unique among active jobs on that kiosk; QR = `PL1:<job>:<token>`; kiosk gets only HMAC hashes | `domain/codes.py` |

## Not in M1 (next milestones)

- Kiosk agent endpoints (heartbeat, next job, status, offline code cache, pickup report) — **M2/M3**
- WebSocket live status — M2
- PIN brute-force lockout at the kiosk — M3
- Alembic migrations — before the first real deployment (M1 uses `create_all`)
