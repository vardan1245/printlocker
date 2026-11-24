import hashlib
import io
import json
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.api import ApiClient, ApiError, OfflineError  # noqa: E402
from agent.hardware import SimHardware  # noqa: E402
from agent.layout import build_pdf, page_sequence, sheet_count, split_sequence  # noqa: E402
from agent.printers import CupsPrinter, PrintError, SimPrinter  # noqa: E402
from agent.simview import LogBuffer, SimHolder, SimState, serve  # noqa: E402
from agent.worker import PrinterWorker, process_job  # noqa: E402


def make_pdf(pages: int) -> bytes:
    w = PdfWriter()
    for i in range(pages):
        w.add_blank_page(width=595 + i, height=842)  # width encodes the page number
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def widths(pdf: bytes) -> list[int]:
    return [round(float(p.mediabox.width)) - 595 + 1 for p in PdfReader(io.BytesIO(pdf)).pages]


# ---------------------------------------------------------------- layout

def test_sequence_simplex_and_duplex():
    assert page_sequence(3, None, 2, False) == [1, 2, 3, 1, 2, 3]
    assert page_sequence(3, None, 2, True) == [1, 2, 3, None, 1, 2, 3, None]
    assert page_sequence(5, "2-3", 1, True) == [2, 3]
    assert sheet_count(page_sequence(3, None, 2, True), True) == 4  # matches backend


@pytest.mark.parametrize("pages,copies,duplex", [(700, 1, False), (250, 3, True), (3, 1, True), (999, 2, False)])
def test_sheet_count_matches_backend_rule(pages, copies, duplex):
    per_copy = -(-pages // 2) if duplex else pages
    assert sheet_count(page_sequence(pages, None, copies, duplex), duplex) == per_copy * copies


def test_split_by_sheets():
    seq = page_sequence(7, None, 1, True)  # 4 sheets
    parts = split_sequence(seq, [2, 2], True)
    assert parts == [[1, 2, 3, 4], [5, 6, 7, None]]
    with pytest.raises(ValueError):
        split_sequence(seq, [2, 1], True)


def test_build_pdf_with_blanks():
    src = make_pdf(3)
    out = build_pdf(src, [3, 1, None])
    assert len(PdfReader(io.BytesIO(out)).pages) == 3
    assert widths(out)[:2] == [3, 1]


# ---------------------------------------------------------------- job processing (fake API object)

class FakeApi:
    def __init__(self, pdf: bytes):
        self.pdf = pdf
        self.statuses: list[tuple[str, str | None]] = []

    def set_status(self, job_id, status, error=None):
        self.statuses.append((status, error))
        return {}

    def job_file(self, job_id):
        return self.pdf


def make_job(pdf: bytes, pages: int, parts: list[tuple[int, int]], copies=1, duplex=False, printer_id=1):
    sheets = sum(s for _, s in parts)
    return {
        "id": 7, "printer_id": printer_id, "cups_queue": "a", "status": "queued", "pages": pages,
        "page_range": None, "copies": copies, "duplex": duplex, "sheets": sheets,
        "file_sha256": hashlib.sha256(pdf).hexdigest(),
        "parts": [{"part_no": i, "sheets": s, "compartment_id": 100 + slot, "slot_no": slot}
                  for i, (slot, s) in enumerate(parts, start=1)],
    }


def sim(printer_ids=(1,), slots=5):
    hw = SimHardware(speed=0)
    comps = [{"id": pid * 100 + s, "printer_id": pid, "slot_no": s} for pid in printer_ids for s in range(1, slots + 1)]
    hw.setup(comps, list(printer_ids))
    printers = {pid: SimPrinter(pid, "q", speed=0, on_sheet=hw.add_sheet) for pid in printer_ids}
    return hw, printers


def test_job_to_ready():
    pdf = make_pdf(4)
    hw, printers = sim()
    api = FakeApi(pdf)
    assert process_job(make_job(pdf, 4, [(2, 4)]), api, printers[1], hw) == "ready"
    assert [s for s, _ in api.statuses] == ["printing", "delivering", "ready"]
    assert hw.compartments[102]["paper_sheets"] == 4
    assert hw.columns[1]["elevator_slot"] == 0 and hw.columns[1]["tray_sheets"] == 0


def test_split_job_goes_to_two_compartments():
    pdf = make_pdf(700)
    hw, printers = sim()
    api = FakeApi(pdf)
    assert process_job(make_job(pdf, 700, [(1, 350), (2, 350)]), api, printers[1], hw) == "ready"
    assert hw.compartments[101]["paper_sheets"] == 350
    assert hw.compartments[102]["paper_sheets"] == 350


def test_printer_jam_fails_job():
    pdf = make_pdf(4)
    hw, printers = sim()
    printers[1].fail_next = "paper jam"
    api = FakeApi(pdf)
    assert process_job(make_job(pdf, 4, [(1, 4)]), api, printers[1], hw) == "failed"
    assert api.statuses[-1] == ("failed", "paper jam")
    assert printers[1].blocked and printers[1].report()["status"] == "error"
    printers[1].clear_block()
    hw.clear_column(1)
    assert printers[1].report()["status"] == "ok" and hw.columns[1]["tray_sheets"] == 0


def test_delivery_failure_fails_job():
    pdf = make_pdf(2)
    hw, printers = sim()
    hw.fail_next_delivery = "elevator stalled"
    api = FakeApi(pdf)
    assert process_job(make_job(pdf, 2, [(3, 2)]), api, printers[1], hw) == "failed"
    assert api.statuses[-1] == ("failed", "elevator stalled")
    assert [s for s, _ in api.statuses] == ["printing", "delivering", "failed"]
    assert printers[1].blocked  # paper is still on the tray


def test_blocked_printer_takes_no_jobs():
    class Api:
        calls = 0

        def next_job(self, pid):
            Api.calls += 1
            return None

    hw, printers = sim()
    printers[1].blocked = "jam"
    stop = threading.Event()
    w = PrinterWorker(Api(), printers[1], hw, 0.01, stop)
    w.start()
    time.sleep(0.1)
    stop.set()
    w.join(1)
    assert Api.calls == 0


def test_out_of_paper_fails_and_blocks():
    pdf = make_pdf(1)
    hw, printers = sim()
    printers[1].paper_ok = False
    api = FakeApi(pdf)
    assert process_job(make_job(pdf, 1, [(1, 1)]), api, printers[1], hw) == "failed"
    assert "paper" in api.statuses[-1][1]


def test_corrupted_download_fails_job():
    pdf = make_pdf(2)
    hw, printers = sim()
    job = make_job(pdf, 2, [(1, 2)])
    api = FakeApi(pdf + b"x")
    assert process_job(job, api, printers[1], hw) == "failed"
    assert "checksum" in api.statuses[-1][1]


# ---------------------------------------------------------------- CUPS driver with a fake lp/lpstat

def test_cups_driver_commands():
    calls = []
    state = {"pending": True}

    class R:
        def __init__(self, out="", code=0, err=""):
            self.stdout, self.returncode, self.stderr = out, code, err

    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "lp":
            return R("request id is printer_a-42 (1 file(s))")
        if cmd[:3] == ["lpstat", "-W", "not-completed"]:
            out = "printer_a-42 user 1024 now" if state["pending"] else ""
            state["pending"] = False
            return R(out)
        if cmd[:3] == ["lpstat", "-W", "completed"]:
            return R("printer_a-42 user 1024 done")
        return R("printer printer_a is idle. enabled")

    CupsPrinter(1, "printer_a", run=run, poll=0).print_pdf(make_pdf(1), sheets=1, duplex=True, title="t")
    lp = calls[0]
    assert lp[:3] == ["lp", "-d", "printer_a"] and "sides=two-sided-long-edge" in lp


def test_cups_driver_lp_error():
    class R:
        stdout, returncode, stderr = "", 1, "lp: printer not found"
    with pytest.raises(PrintError, match="not found"):
        CupsPrinter(1, "x", run=lambda *a, **k: R()).print_pdf(make_pdf(1), sheets=1, duplex=False, title="t")


# ---------------------------------------------------------------- full loop over HTTP against a fake backend

class FakeBackend:
    """Implements just enough of /api/v1/kiosk/* for the agent."""

    def __init__(self, key: str):
        self.key = key
        self.pdf = make_pdf(6)
        self.jobs = {
            1: make_job(self.pdf, 6, [(1, 6)], printer_id=1) | {"id": 1},
            2: make_job(self.pdf, 6, [(1, 3), (2, 3)], printer_id=2) | {"id": 2},
        }
        self.history: list[tuple[int, str]] = []
        self.heartbeats = 0

    def handle(self, method, path, body):
        if path == "/api/v1/kiosk/config":
            return 200, {"kiosk_id": 1, "name": "Test", "code_key": "k", "pickup_hours": 24,
                         "printers": [{"id": 1, "name": "A", "cups_queue": "a", "duplex": True},
                                      {"id": 2, "name": "B", "cups_queue": "b", "duplex": True}],
                         "compartments": [{"id": pid * 100 + s, "printer_id": pid, "slot_no": s,
                                           "capacity_sheets": 500, "state": "free"}
                                          for pid in (1, 2) for s in (1, 2, 3)]}
        if path == "/api/v1/kiosk/heartbeat":
            self.heartbeats += 1
            return 200, {"ok": True}
        if path.startswith("/api/v1/kiosk/jobs/next"):
            pid = int(path.split("printer_id=")[1])
            for j in self.jobs.values():
                if j["printer_id"] == pid and j["status"] == "queued":
                    return 200, j
            return 200, None
        parts = path.split("/")
        if parts[-1] == "file":
            return 200, self.pdf
        if parts[-1] == "status":
            jid = int(parts[-2])
            self.jobs[jid]["status"] = body["status"]
            self.history.append((jid, body["status"]))
            return 200, self.jobs[jid]
        return 404, {"detail": "not found"}


@pytest.fixture
def backend():
    fb = FakeBackend("secret")

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _do(self, method):
            if self.headers.get("X-Kiosk-Key") != fb.key:
                code, payload = 401, {"detail": "invalid kiosk key"}
            else:
                n = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(n)) if n else None
                code, payload = fb.handle(method, self.path, body)
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._do("GET")

        def do_POST(self):
            self._do("POST")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    fb.url = f"http://127.0.0.1:{srv.server_address[1]}/api/v1"
    yield fb
    srv.shutdown()


def test_api_client_auth_and_offline(backend):
    with pytest.raises(ApiError) as e:
        ApiClient(backend.url, "wrong").config()
    assert e.value.status == 401
    with pytest.raises(OfflineError):
        ApiClient("http://127.0.0.1:9/api/v1", "x", timeout=1).config()


def test_agent_end_to_end_over_http(backend):
    api = ApiClient(backend.url, "secret")
    conf = api.config()
    hw = SimHardware(speed=0)
    hw.setup(conf["compartments"], [p["id"] for p in conf["printers"]])
    printers = [SimPrinter(p["id"], p["cups_queue"], speed=0, on_sheet=hw.add_sheet) for p in conf["printers"]]
    stop = threading.Event()
    workers = [PrinterWorker(api, p, hw, 0.05, stop) for p in printers]
    for w in workers:
        w.start()
    deadline = time.time() + 10
    while time.time() < deadline and any(j["status"] != "ready" for j in backend.jobs.values()):
        time.sleep(0.05)
    stop.set()
    assert all(j["status"] == "ready" for j in backend.jobs.values())
    assert [s for j, s in backend.history if j == 2] == ["printing", "delivering", "ready"]
    assert hw.compartments[201]["paper_sheets"] == 3 and hw.compartments[202]["paper_sheets"] == 3
    assert hw.compartments[101]["paper_sheets"] == 6


# ---------------------------------------------------------------- simulator page

def test_simview_state_and_actions():
    hw, printers = sim(printer_ids=(1,), slots=2)
    hw.compartments[101]["paper_sheets"] = 5
    server = serve(SimState(hw, printers, {1: "A"}, LogBuffer(), "Demo"), 0)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        st = json.loads(urllib.request.urlopen(base + "/state").read())
        assert st["kiosk"] == "Demo" and len(st["compartments"]) == 2
        assert b"PrintLocker kiosk simulator" in urllib.request.urlopen(base + "/").read()
        req = urllib.request.Request(base + "/action", method="POST",
                                     data=json.dumps({"type": "take_paper", "compartment_id": 101}).encode())
        assert json.loads(urllib.request.urlopen(req).read())["ok"]
        assert hw.compartments[101]["paper_sheets"] == 0
    finally:
        server.shutdown()


def test_simview_connecting_before_config():
    holder = SimHolder()
    server = serve(holder, 0)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert json.loads(urllib.request.urlopen(base + "/state").read()) == {"connecting": True}
        hw, printers = sim(printer_ids=(1,), slots=1)
        holder.sim = SimState(hw, printers, {1: "A"}, LogBuffer(), "Demo")
        assert json.loads(urllib.request.urlopen(base + "/state").read())["kiosk"] == "Demo"
    finally:
        server.shutdown()
