"""Controller protocol conformance tests: the acceptance tests for the ESP32 firmware (M8).

By default they run against the controller twin (agent/twin.py). Point them at a real board with
    PL_CONFORMANCE_PORT=/dev/ttyUSB0 pytest tests/test_controller_protocol.py
(the board must be wired for 1 column x 8 compartments; tests that need a person at a door are skipped).
"""
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.twin import ControllerTwin, TwinLink  # noqa: E402

REAL = os.environ.get("PL_CONFORMANCE_PORT")
LOCKS = [{"lock": i, "column": 1, "slot": i} for i in range(1, 9)]


class Link:
    def __init__(self):
        if REAL:
            import serial

            self.port = serial.Serial(REAL, 115200, timeout=0.5)
            self.twin = None
        else:
            self.twin = ControllerTwin(speed=0)
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
    return lk


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
    lk = Link()
    lk.twin.speed = 0.02
    lk.call(cmd="config", locks=LOCKS)
    first = lk.send(cmd="deliver", column=1, slot=1, sheets=1)
    assert lk.wait(lambda m: m.get("id") == first)["ok"]
    r = lk.call(cmd="deliver", column=1, slot=2, sheets=1)
    assert r["ok"] is False and "busy" in r["error"]
    lk.wait(lambda m: m.get("ref") == first, timeout=5)


def test_unlock_answers_while_a_delivery_runs():
    if REAL:
        pytest.skip("timing test for the twin; on the board run it with a person watching")
    lk = Link()
    lk.twin.speed = 0.02  # a 26 s delivery takes ~0.5 s
    lk.call(cmd="config", locks=LOCKS)
    mid = lk.send(cmd="deliver", column=1, slot=5, sheets=1)
    lk.wait(lambda m: m.get("id") == mid)
    t0 = time.monotonic()
    assert lk.call(cmd="unlock", lock=8)["ok"]
    assert time.monotonic() - t0 < 0.2
    lk.twin.user_closes(8)
    lk.wait(lambda m: m.get("ref") == mid, timeout=10)
