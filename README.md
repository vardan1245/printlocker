# PrintLocker

Self-service A4 print kiosk with QR-unlocked pickup compartments.

| Milestone | Status |
| --- | --- |
| M1 Backend core | done |
| M2 Kiosk agent + hardware simulator | done |
| M3 Pickup (touchscreen, QR/PIN, offline) | done |
| M4 Flutter app | done |
| M5 Admin panel (`/admin` on the API: live overview, open doors, clear printers, jobs, events, setup) | done |
| M6 Real print | waiting for the printer |
| M7 Hardware design ([`hardware/`](hardware/README.md): 1 printer + 8 compartments, Fusion model script, BOM, wiring) | draft done |
| M8 Firmware · M9 Prototype | later |

**Live test setup (Render + Vercel, free plans; first visit after idle takes ~1 min):**
- User app (Flutter web): https://printlocker-app.vercel.app · Android APK: GitHub Actions → "Flutter app" run → Artifacts
- Test page (log in, print, see QR + PIN): https://printlocker-api-xpqo.onrender.com
- Kiosk simulator with touchscreen: https://printlocker-kiosk-sim-xpqo.onrender.com

```
printlocker/
├── docker-compose.yml      backend + PostgreSQL + one simulated kiosk
├── app/                    Flutter user app (Android, iOS, web) - see app/README.md
├── backend/                FastAPI API (users, kiosks, jobs, kiosk-agent endpoints, live status)
│   ├── app/domain/         pure business rules (states, sheets, reservation, PDF, codes, storage)
│   ├── app/services/       use cases on the database
│   ├── app/api/            routes: auth, kiosks, jobs, admin, kiosk (agent), ws (live status)
│   └── tests/
└── kiosk-agent/            program that runs on each kiosk's Raspberry Pi
    ├── agent/main.py       start-up, heartbeat, one worker thread per printer
    ├── agent/worker.py     one job: download → print part by part → elevator → report
    ├── agent/layout.py     which pages go into which compartment (split jobs, duplex blanks)
    ├── agent/printers.py   SimPrinter and CupsPrinter (lp / lpstat)
    ├── agent/hardware.py   SimHardware and SerialHardware (ESP32, JSON lines)
    ├── agent/pickup.py     offline code cache, QR/PIN check, PIN lockout, doors, pickup outbox
    ├── agent/kiosk.html    7" touchscreen page (scan QR / enter PIN)
    ├── agent/simview.*     local web server + simulator page
    └── tests/
```

## Run everything (Docker)

```bash
cd backend && cp .env.example .env
# put a key into PL_FILE_KEY:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
cd .. && docker compose up --build
```

- API docs: http://localhost:8000/docs
- Kiosk simulator: http://localhost:8100
- Demo user: `demo@example.com` / `demo12345` · admin: from `PL_ADMIN_EMAIL` / `PL_ADMIN_PASSWORD`

Try it: in `/docs`, log in as the demo user, `GET /kiosks`, then `POST /jobs` with a PDF and a
printer id. Watch the simulator: the printer prints, the elevator carries the stack down, it is
pushed into the compartment, and the job becomes `ready`. `GET /jobs/{id}/pickup` shows the QR
text and PIN.

The simulator page also lets you jam a printer, fail a delivery, run out of paper, and act as the
operator who clears a fault.

## Kiosk agent

Runs on the Raspberry Pi. Settings are environment variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `PL_SERVER_URL` | `http://localhost:8000/api/v1` | backend |
| `PL_KIOSK_KEY` | — | key shown once when the kiosk was created |
| `PL_PRINTER_MODE` | `sim` | `sim` or `cups` (real printers; CUPS queue name = printer's `cups_queue`) |
| `PL_HARDWARE_MODE` | `twin` | `twin` (controller twin: real timings, interlocks, faults), `sim` (instant stand-in) or `serial` (ESP32 controller) |
| `PL_SERIAL_PORT` | `/dev/ttyUSB0` | ESP32 port |
| `PL_SIM_SPEED` | `1.0` | simulator timing: `1` = real machine time (a delivery takes 17–33 s), `0.2` = 5× faster, `0` = instant |
| `PL_SIM_VIEW_PORT` | `8100` | simulator page; `0` = off |
| `PL_POLL_SECONDS` / `PL_HEARTBEAT_SECONDS` | `2` / `15` | polling intervals |
| `PL_CUPS_OPTIONS` | `outputorder=reverse` | extra `lp -o` options, space-separated (add the PPD's face-up/rear output-bin option) |

**Printer:** Kyocera ECOSYS PA4500x with the PT-320 face-up rear tray position as the paper exit
(see the master spec). Face-up output reverses the stack, hence `outputorder=reverse`.

Without Docker: `cd kiosk-agent && pip install -r requirements.txt && PL_KIOSK_KEY=... python -m agent.main`

How a job runs:
1. Each printer has its own worker thread that asks `GET /kiosk/jobs/next?printer_id=…`.
2. It reports `printing`, downloads the PDF and checks its SHA-256.
3. It builds the face sequence (page range × copies; a blank back when a duplex copy has an odd
   page count, so every copy starts on a new sheet) and cuts it into the parts the server
   reserved (max 500 sheets per compartment).
4. For each part: print that part's own PDF → the elevator carries the stack to the compartment →
   the pusher slides it in. Before the last delivery it reports `delivering`.
5. It reports `ready`. The server then shows the user the QR and PIN.

**Safety rule:** after a paper jam, an empty paper tray or a failed delivery, the job is marked
`failed` and that printer column is **blocked**: it reports `error` in its heartbeat (so the
server stops accepting jobs for it) and takes no new jobs until an operator removes leftover paper
and clears it. This stops one user's pages from landing on top of another user's leftovers.
An operator clears it with **Clear printer** in the admin panel (`/admin`); the kiosk picks the
command up within a few seconds. Restarting the agent also clears it.

ESP32 serial protocol (one JSON object per line) is documented in `kiosk-agent/agent/hardware.py`.

### Pickup (M3)

The touchscreen page is served by the agent at `http://localhost:<port>/kiosk`; on the real kiosk
Chromium shows it full-screen. The USB QR scanner acts as a keyboard: it types the code and Enter.

1. The agent keeps the active codes of its own ready jobs (`GET /kiosk/codes`, HMAC hashes only)
   in `PL_DATA_DIR/codes.json`, refreshed every 10 s and as soon as a job becomes ready.
2. QR (`PL1:<job>:<token>`) or 6-digit PIN is checked locally, so pickup works offline.
3. A code works once, even offline. 5 wrong PINs in 10 min lock PIN entry for 5 min (QR still works).
4. The job's compartment(s) unlock; when all its doors are closed again (or after 3 min) the pickup
   is written to `PL_DATA_DIR/outbox.json` and sent to `POST /kiosk/pickup`, retried until the
   server answers. A late report for a job the server already expired is accepted.
5. Paper still detected after the door closes shows up as a compartment fault via the heartbeat.

**Restart recovery:** on start-up the agent calls `POST /kiosk/recover`; jobs that were
`printing`/`delivering` are failed and their printer columns are blocked until cleared.

## Backend API

User app: `/api/v1/auth/*`, `/kiosks`, `/jobs` (+ `/preview`, `/{id}/pickup`), WebSocket
`/api/v1/ws/user?token=<access token>` for live job status.

Kiosk agent (header `X-Kiosk-Key`): `GET /kiosk/config`, `POST /kiosk/heartbeat`,
`GET /kiosk/jobs/next`, `GET /kiosk/jobs/{id}/file`, `POST /kiosk/jobs/{id}/status`
(`printing` / `delivering` / `ready` / `failed`, repeat-safe), `GET /kiosk/codes`,
`POST /kiosk/pickup` (repeat-safe).

Admin: create kiosks and printers, list compartments and jobs, move a job by hand,
mark a compartment emptied, run expiry now.

A kiosk counts as online if it sent a heartbeat in the last 90 s. If a kiosk reports paper in a
compartment the server thinks is free, that compartment is set to `fault`.

## Tests

```bash
cd backend && pip install -r requirements.txt && pytest -q
cd kiosk-agent && pip install -r requirements.txt && pytest -q
```

## Known gaps

- Live status runs in one backend process; several processes would need Redis pub/sub.
- Alembic migrations come before the first real deployment (tables are created on start-up).
