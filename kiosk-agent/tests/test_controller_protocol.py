"""Controller protocol conformance tests: the acceptance tests for the ESP32 firmware (M8).

Three targets, same tests:
    pytest tests/test_controller_protocol.py                                   # the twin (agent/twin.py)
    PL_CONFORMANCE_CMD=../firmware/build/controller_native pytest ...          # the firmware on a PC
    PL_CONFORMANCE_PORT=/dev/ttyUSB0 pytest ...                                # the firmware on the ESP32
The board must be wired for 1 column x 8 compartments; tests that need a person at a door are skipped.
"""
import json
import os
import queue
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.twin import ControllerTwin, TwinLink  # noqa: E402

REAL = os.environ.get("PL_CONFORMANCE_PORT")
CMD = os.environ.get("PL_CONFORMANCE_CMD")
LOCKS = [{"lock": i, "column": 1, "slot": i} for i in range(1, 9)]


class ProcPort:
    """The PC build of the firmware as a subprocess, talking on stdin/stdout."""

    def __init__(self, cmd: str, env: dict | None = None):
        self.p = subprocess.Popen(shlex.split(cmd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  env={**os.environ, **(env or {})})
        self.q: queue.Queue[bytes] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.p.stdout:
            self.q.put(line)

    def write(self, data: bytes):
        self.p.stdin.write(data)
        self.p.stdin.flush()

    def readline(self) -> bytes:
        try:
            return self.q.get(timeout=0.5)
        except queue.Empty:
            return b""

    def close(self):
        self.p.kill()


class Link:
    def __init__(self, env: dict | None = None, slow: bool = False):
        """slow: run the mechanism at 2% of real time, so a delivery lasts about half a second."""
        if REAL:
            import serial

            self.port = serial.Serial(REAL, 115200, timeout=0.5)
            self.twin = None
        elif CMD:
            self.port = ProcPort(CMD + (" --speed 0.02" if slow else ""), env)
            self.twin = None
        else:
            self.twin = ControllerTwin(speed=0.02 if slow else 0)
            self.port = TwinLink(self.twin)
        self.ids = iter(range(1, 10_000))
        self.seen: list[dict] = []

    def send(self, **cmd) -> int:
        mid = next(self.ids)
        self.port.write((json.dumps({"id": mid, **cmd}) + "\n").encode())
        return mid

    def wait(self, pred, timeout: float = 5.0) -> dict:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for m in self.seen:
                if pred(m):
                    self.seen.remove(m)
                    return m
            line = self.port.readline().decode(errors="replace").strip()
            if not line:
                continue
            try:
                self.seen.append(json.loads(line))
            except ValueError:
                pass  # free-text debug output is allowed
        raise AssertionError("no matching message from the controller")

    def call(self, **cmd) -> dict:
        mid = self.send(**cmd)
        return self.wait(lambda m: m.get("id") == mid)


@pytest.fixture
def link():
    lk = Link()
    assert lk.call(cmd="config", locks=LOCKS)["ok"]
    yield lk
    if hasattr(lk.port, "close"):
        lk.port.close()


def wait_idle(lk: Link, timeout: float = 60) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not any(c["busy"] for c in lk.call(cmd="status")["columns"]):
            return
        time.sleep(0.05)
    raise AssertionError("column still busy")


def deliver(lk: Link, slot: int, sheets: int = 1, timeout: float = 90) -> dict:
    mid = lk.send(cmd="deliver", column=1, slot=slot, sheets=sheets)
    assert lk.wait(lambda m: m.get("id") == mid, timeout=2)["ok"]
    return lk.wait(lambda m: m.get("ref") == mid and m.get("event") in ("delivered", "delivery_failed"),
                   timeout=timeout)


def test_every_reply_carries_the_request_id(link):
    r = link.call(cmd="sensors")
    assert r["ok"] is True and isinstance(r["sensors"], list)


def test_sensors_report_every_lock(link):
    sensors = link.call(cmd="sensors")["sensors"]
    assert sorted(s["lock"] for s in sensors) == list(range(1, 9))
    for s in sensors:
        assert isinstance(s["door_closed"], bool) and isinstance(s["paper"], bool)


def test_unknown_command_is_an_error_not_a_crash(link):
    r = link.call(cmd="dance")
    assert r["ok"] is False and r["error"]
    assert link.call(cmd="sensors")["ok"]  # still alive


def test_bad_arguments_are_an_error(link):
    assert link.call(cmd="unlock")["ok"] is False
    assert link.call(cmd="deliver", column=1, slot=99, sheets=1)["ok"] is False


def test_garbage_lines_are_ignored(link):
    link.port.write(b"this is not json\n")
    assert link.call(cmd="sensors")["ok"]


def test_deliver_is_accepted_at_once_and_finishes_with_an_event(link):
    mid = link.send(cmd="deliver", column=1, slot=3, sheets=10)
    acc = link.wait(lambda m: m.get("id") == mid, timeout=2)
    assert acc["ok"] and acc.get("accepted") is True
    done = link.wait(lambda m: m.get("ref") == mid and m.get("event") in ("delivered", "delivery_failed"), timeout=90)
    assert done["event"] == "delivered", done
    paper = {s["lock"]: s["paper"] for s in link.call(cmd="sensors")["sensors"]}
    assert paper[3] is True


def test_second_deliver_on_a_busy_column_is_refused():
    if REAL:
        pytest.skip("needs a slow delivery; covered on the real board by test_deliver_*")
    lk = Link(slow=True)
    lk.call(cmd="config", locks=LOCKS)
    first = lk.send(cmd="deliver", column=1, slot=1, sheets=1)
    assert lk.wait(lambda m: m.get("id") == first)["ok"]
    r = lk.call(cmd="deliver", column=1, slot=2, sheets=1)
    assert r["ok"] is False and "busy" in r["error"]
    lk.wait(lambda m: m.get("ref") == first, timeout=10)
    if hasattr(lk.port, "close"):
        lk.port.close()


def test_unlock_answers_while_a_delivery_runs():
    if REAL:
        pytest.skip("timing test; on the board run it with a person watching")
    lk = Link(slow=True)
    lk.call(cmd="config", locks=LOCKS)
    mid = lk.send(cmd="deliver", column=1, slot=5, sheets=1)
    lk.wait(lambda m: m.get("id") == mid)
    time.sleep(0.15)
    t0 = time.monotonic()
    assert lk.call(cmd="unlock", lock=8)["ok"]
    assert time.monotonic() - t0 < 0.2
    if lk.twin:
        lk.twin.user_closes(8)  # the twin's simulated user opened the door; shut it so the lift can go on
    assert lk.wait(lambda m: m.get("ref") == mid, timeout=10)["event"] == "delivered"
    if hasattr(lk.port, "close"):
        lk.port.close()


@pytest.mark.parametrize("slot,design_s", [(1, 17.25), (8, 33.0)])
def test_delivery_time_is_within_15_percent_of_the_design(link, slot, design_s):
    done = deliver(link, slot)
    assert done["event"] == "delivered"
    assert abs(done["seconds"] - design_s) <= 0.15 * design_s, done


def test_step_events_describe_each_move(link):
    mid = link.send(cmd="deliver", column=1, slot=2, sheets=1)
    steps = []
    while True:
        m = link.wait(lambda m: m.get("event") in ("step", "delivered", "delivery_failed")
                      and (m.get("ref") in (None, mid)), timeout=90)
        if m["event"] != "step":
            break
        steps.append(m["step"])
    assert steps == ["settle", "lift_up", "push", "pusher_home", "lift_down"]


def test_status_reports_the_mechanism(link):
    st = link.call(cmd="status")
    assert st["ok"] and st["columns"][0]["busy"] is False and st["columns"][0]["error"] is None


@pytest.mark.skipif(not CMD, reason="fault injection through the PC build's environment")
@pytest.mark.parametrize("fault,message", [("lift_stall", "lift stalled"), ("pusher_stall", "pusher stalled"),
                                           ("stack_missing", "sees no paper")])
def test_faults_fail_the_delivery_and_need_an_operator(fault, message):
    lk = Link(env={"PL_SIM_FAULT": fault})
    lk.call(cmd="config", locks=LOCKS)
    done = deliver(lk, 4)
    assert done["event"] == "delivery_failed" and message in done["error"]
    r = lk.call(cmd="deliver", column=1, slot=4, sheets=1)
    assert r["ok"] is False and "needs an operator" in r["error"]
    assert lk.call(cmd="clear", column=1)["ok"]
    wait_idle(lk)  # clear parks the mechanism first (pusher home, lift down)
    assert deliver(lk, 4)["event"] == "delivered"
    lk.port.close()


@pytest.mark.skipif(not CMD, reason="sheet counter is forced through the PC build's environment")
def test_sheet_count_mismatch_stops_before_moving():
    lk = Link(env={"PL_SIM_SHEETS": "3"})
    lk.call(cmd="config", locks=LOCKS)
    done = deliver(lk, 2, sheets=4)
    assert done["event"] == "delivery_failed" and "counted 3 sheets, expected 4" in done["error"]
    lk.port.close()


@pytest.mark.skipif(not (CMD or REAL), reason="the boot message comes from the firmware")
def test_boot_message_names_the_firmware_version():
    lk = Link()
    boot = lk.wait(lambda m: m.get("event") == "boot", timeout=5)
    assert boot["version"]
    if hasattr(lk.port, "close"):
        lk.port.close()
