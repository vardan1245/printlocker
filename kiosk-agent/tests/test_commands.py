import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.commands import run_command  # noqa: E402
from agent.hardware import SimHardware  # noqa: E402
from agent.printers import CupsPrinter, SimPrinter  # noqa: E402


def setup():
    hw = SimHardware(speed=0)
    hw.setup([{"id": 101, "printer_id": 1, "slot_no": 1}], [1])
    return hw, {1: SimPrinter(1, "q", speed=0, on_sheet=hw.add_sheet)}


def test_unlock_opens_door():
    hw, printers = setup()
    assert run_command({"id": 1, "type": "unlock", "payload": {"compartment_id": 101, "slot_no": 1}}, hw, printers) == (True, None)
    assert hw.compartments[101]["locked"] is False


def test_clear_printer_unblocks_and_empties_tray():
    hw, printers = setup()
    printers[1].blocked = "job 3: paper jam"
    hw.columns[1]["tray_sheets"] = 12
    rep = printers[1].report()
    assert rep["status"] == "error" and rep["message"] == "job 3: paper jam"
    assert run_command({"id": 2, "type": "clear_printer", "payload": {"printer_id": 1}}, hw, printers) == (True, None)
    assert printers[1].blocked is None and hw.columns[1]["tray_sheets"] == 0
    assert "message" not in printers[1].report()


def test_bad_commands():
    hw, printers = setup()
    assert run_command({"type": "explode", "payload": {}}, hw, printers)[0] is False
    assert run_command({"type": "unlock", "payload": {}}, hw, printers)[0] is False
    assert run_command({"type": "clear_printer", "payload": {"printer_id": 9}}, hw, printers)[0] is False


def test_cups_report_has_reason_when_blocked():
    class R:
        stdout, returncode, stderr = "printer q is idle. enabled", 0, ""
    drv = CupsPrinter(1, "q", run=lambda *a, **k: R())
    assert drv.report() == {"id": 1, "status": "ok"}
    drv.blocked = "job 5: elevator stalled"
    assert drv.report()["message"] == "job 5: elevator stalled"
