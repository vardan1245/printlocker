"""The agent driving the controller twin: real geometry, timings, interlocks and faults."""
import hashlib
import io
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest
from pypdf import PdfWriter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.hardware import HardwareError, TwinHardware  # noqa: E402
from agent.printers import SimPrinter  # noqa: E402
from agent.twin import Geometry  # noqa: E402
from agent.worker import process_job  # noqa: E402


def make_hw(speed=0.0, slots=8):
    hw = TwinHardware(speed=speed, timeout=30)
    comps = [{"id": 100 + s, "printer_id": 1, "slot_no": s} for s in range(1, slots + 1)]
    hw.setup(comps, [1])
    return hw


def put_sheets(hw, n):
    for _ in range(n):
        hw.add_sheet(1)


@pytest.mark.parametrize("slot,expected", [(1, 17.25), (5, 26.25), (8, 33.0)])
def test_delivery_time_matches_the_mechanics_calculation(slot, expected):
    # settle 5 s + lift up/down at 80 mm/s + 2 x 320 mm push at 100 mm/s, 0.4 s ramps per move
    g = Geometry()
    travel = g.floor(slot) - g.receive_mm
    assert expected == pytest.approx(g.settle_s + 2 * (travel / 80 + 0.4) + 2 * (320 / 100 + 0.4))
    hw = make_hw()
    put_sheets(hw, 4)
    assert hw.deliver(1, slot, 4) == pytest.approx(expected, abs=0.01)
    c = hw.snapshot()["columns"]["1"]
    assert c["lift_mm"] == g.receive_mm and c["pusher_mm"] == 0 and c["tray_sheets"] == 0 and not c["hold"]
    assert hw.compartments[100 + slot]["paper_sheets"] == 4


def test_job_to_ready_through_the_twin():
    w = PdfWriter()
    for _ in range(6):
        w.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    w.write(buf)
    pdf = buf.getvalue()

    class Api:
        statuses = []

        def set_status(self, job_id, status, error=None):
            self.statuses.append((status, error))

        def job_file(self, job_id):
            return pdf

    hw = make_hw()
    printer = SimPrinter(1, "q", speed=0, on_sheet=hw.add_sheet)
    job = {"id": 9, "printer_id": 1, "cups_queue": "q", "status": "queued", "pages": 6, "page_range": None,
           "copies": 1, "duplex": True, "sheets": 3, "file_sha256": hashlib.sha256(pdf).hexdigest(),
           "parts": [{"part_no": 1, "sheets": 3, "compartment_id": 104, "slot_no": 4}]}
    api = Api()
    assert process_job(job, api, printer, hw) == "ready"
    assert [s for s, _ in api.statuses] == ["printing", "delivering", "ready"]
    assert hw.compartments[104]["paper_sheets"] == 3


def test_sheet_count_mismatch_stops_the_delivery():
    hw = make_hw()
    put_sheets(hw, 3)  # the printer jammed one sheet
    with pytest.raises(HardwareError, match="counted 3 sheets, expected 4"):
        hw.deliver(1, 2, 4)
    assert hw.snapshot()["columns"]["1"]["error"]
    with pytest.raises(HardwareError, match="needs an operator"):
        hw.deliver(1, 2, 4)
    hw.clear_column(1)
    put_sheets(hw, 4)
    hw.deliver(1, 2, 4)


def test_open_door_blocks_the_lift():
    hw = make_hw()
    hw.unlock(107)  # someone is collecting from compartment 7
    put_sheets(hw, 2)
    with pytest.raises(HardwareError, match="door 7 stayed open"):
        hw.deliver(1, 2, 2)


def test_lift_pauses_while_a_door_is_open_and_resumes():
    hw = make_hw(speed=0.02)  # 26 s of machine time takes about half a second
    events = []
    out = hw.twin.out
    hw.twin.out = lambda line: (events.append(json.loads(line)), out(line))
    put_sheets(hw, 2)
    t = threading.Thread(target=hw.deliver, args=(1, 6, 2))
    t.start()
    time.sleep(0.15)  # lift is rising
    hw.unlock(103)
    time.sleep(0.2)
    hw.user_takes_paper_and_closes(103)
    t.join(10)
    kinds = [e.get("event") for e in events]
    assert "paused" in kinds and "resumed" in kinds and kinds[-1] == "delivered"
    assert hw.compartments[106]["paper_sheets"] == 2


@pytest.mark.parametrize("kind,message", [
    ("lift_stall", "lift stalled"), ("pusher_stall", "pusher stalled"),
    ("power_cut", "power lost"), ("stack_missing", "sees no paper")])
def test_faults_fail_the_delivery_and_block_the_column(kind, message):
    hw = make_hw()
    hw.inject_fault(kind)
    put_sheets(hw, 5)
    with pytest.raises(HardwareError, match=message):
        hw.deliver(1, 4, 5)
    col = hw.snapshot()["columns"]["1"]
    assert message in col["error"] and not col["busy"]
    if kind == "power_cut":  # screws back-drive: the carriage ends on the bottom stop with the paper on it
        assert col["lift_mm"] == Geometry().receive_mm and col["tray_sheets"] == 5 and not col["hold"]
    hw.clear_column(1)
    assert hw.snapshot()["columns"]["1"]["error"] is None


def test_unlock_is_instant_during_a_delivery():
    hw = make_hw(speed=0.02)
    put_sheets(hw, 1)
    t = threading.Thread(target=hw.deliver, args=(1, 8, 1))
    t.start()
    time.sleep(0.1)
    t0 = time.monotonic()
    hw.sensors()
    hw.unlock(101)
    assert time.monotonic() - t0 < 0.2
    hw.close_door(101)
    t.join(10)
    assert not t.is_alive()


@pytest.mark.skipif(not hasattr(os, "openpty"), reason="needs a pty")
def test_agent_serial_path_over_a_pty():
    serial = pytest.importorskip("serial")
    from agent.hardware import SerialHardware
    from agent.twin_pty import serve

    path, twin, _ = serve(speed=0)
    hw = SerialHardware(path, timeout=10)
    hw.setup([{"id": 100 + s, "printer_id": 1, "slot_no": s} for s in range(1, 9)], [1])
    for _ in range(3):
        twin.sheet(1)
    assert hw.deliver(1, 2, 3) == pytest.approx(19.5, abs=0.01)
    assert {s["id"]: s["paper_present"] for s in hw.sensors()}[102] is True
    assert serial  # pyserial really was used
