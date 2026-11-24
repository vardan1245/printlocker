import json
import urllib.error
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.api import ApiError, OfflineError  # noqa: E402
from agent.hardware import SimHardware  # noqa: E402
from agent.pickup import CodeCache, Outbox, PickupManager, PinLimiter, hash_code  # noqa: E402
from agent.simview import SimHolder, serve  # noqa: E402

KEY = "kiosk-code-key"


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def code(job_id, token, pin, comps, slots, expires):
    return {"job_id": job_id, "qr_token_hash": hash_code(KEY, token), "pin_hash": hash_code(KEY, pin),
            "compartment_ids": comps, "slots": slots, "expires_at": iso(expires)}


@pytest.fixture
def setup(tmp_path):
    clock = Clock()
    hw = SimHardware(speed=0)
    hw.setup([{"id": 101, "printer_id": 1, "slot_no": 1}, {"id": 102, "printer_id": 1, "slot_no": 2},
              {"id": 103, "printer_id": 1, "slot_no": 3}], [1])
    for cid in (101, 102, 103):
        hw.compartments[cid]["paper_sheets"] = 5
    cache = CodeCache(KEY, tmp_path / "codes.json", clock=clock)
    cache.replace([
        code(7, "tok7", "123456", [101], [1], clock.t + 3600),
        code(8, "tok8", "654321", [102, 103], [2, 3], clock.t + 3600),
        code(9, "tok9", "111111", [101], [1], clock.t - 1),  # already expired
    ])
    outbox = Outbox(tmp_path / "outbox.json")
    pm = PickupManager(cache, hw, outbox, PinLimiter(clock=clock), clock=clock)
    return pm, hw, cache, outbox, clock, tmp_path


def test_qr_opens_right_compartment_once(setup):
    pm, hw, *_ = setup
    r = pm.scan_qr("PL1:7:tok7")
    assert r.ok and r.slots == [1] and "Compartment #1 is open" in r.message
    assert hw.compartments[101]["locked"] is False and hw.compartments[102]["locked"] is True
    assert not pm.scan_qr("PL1:7:tok7").ok  # one use only


@pytest.mark.parametrize("payload", ["PL1:7:wrong", "PL1:8:tok7", "garbage", "", "PL1:9:tok9", "PL1:99:x"])
def test_bad_or_expired_qr(setup, payload):
    pm, hw, *_ = setup
    assert not pm.scan_qr(payload).ok
    assert all(c["locked"] for c in hw.compartments.values())


def test_pin_opens_split_job_two_doors(setup):
    pm, hw, *_ = setup
    r = pm.enter_pin("654321")
    assert r.ok and r.slots == [2, 3] and "Compartments #2, #3 are open" in r.message
    assert not hw.compartments[102]["locked"] and not hw.compartments[103]["locked"]


def test_pin_lockout_and_recovery(setup):
    pm, hw, cache, outbox, clock, _ = setup
    for _ in range(4):
        r = pm.enter_pin("000000")
        assert not r.ok and r.locked_seconds == 0
    r = pm.enter_pin("000000")  # 5th wrong
    assert r.locked_seconds == 300
    r = pm.enter_pin("123456")  # correct PIN refused while locked
    assert not r.ok and r.locked_seconds > 0
    assert pm.scan_qr("PL1:7:tok7").ok  # QR still works
    clock.t += 301
    assert pm.enter_pin("654321").ok


def test_pin_format(setup):
    pm, *_ = setup
    assert pm.enter_pin("12345").message == "The PIN has 6 digits."
    assert pm.enter_pin("abcdef").message == "The PIN has 6 digits."
    assert pm.limiter.failures == []  # format errors don't count as guesses


def test_door_close_reports_pickup(setup):
    pm, hw, cache, outbox, clock, _ = setup
    pm.enter_pin("654321")
    assert pm.check_doors() == []  # doors still open
    hw.user_takes_paper_and_closes(102)
    assert pm.check_doors() == []  # one of two still open
    hw.user_takes_paper_and_closes(103)
    assert pm.check_doors() == [8]
    assert outbox.items[0]["job_id"] == 8 and outbox.items[0]["method"] == "pin"
    assert pm.status()["open_slots"] == []


def test_door_left_open_reports_after_timeout(setup):
    pm, hw, cache, outbox, clock, _ = setup
    pm.scan_qr("PL1:7:tok7")
    clock.t += PickupManager.DOOR_TIMEOUT + 1
    assert pm.check_doors() == [7]


def test_cache_survives_restart_and_used_codes_stay_blocked(setup):
    pm, hw, cache, outbox, clock, tmp = setup
    assert pm.scan_qr("PL1:7:tok7").ok
    cache2 = CodeCache(KEY, tmp / "codes.json", clock=clock)  # agent restarted, offline
    assert cache2.match_pin("654321")[0] == 8
    assert cache2.match_qr("PL1:7:tok7") is None  # used before the restart
    # server still lists job 7 (pickup report not delivered yet): stays blocked
    cache2.replace([code(7, "tok7", "123456", [101], [1], clock.t + 3600)])
    assert cache2.match_qr("PL1:7:tok7") is None


class FakeApi:
    def __init__(self, mode="ok"):
        self.mode = mode
        self.sent = []

    def pickup(self, job_id, method):
        if self.mode == "offline":
            raise OfflineError("down")
        if self.mode == "500":
            raise ApiError(500, "oops")
        if self.mode == "409":
            raise ApiError(409, "job is 'cancelled'")
        self.sent.append((job_id, method))
        return {}


def test_outbox_retries_offline_and_survives_restart(tmp_path):
    ob = Outbox(tmp_path / "o.json")
    ob.add(1, "qr")
    ob.add(2, "pin")
    assert ob.flush(FakeApi("offline")) == 0 and len(ob.items) == 2
    assert ob.flush(FakeApi("500")) == 0 and len(ob.items) == 2
    ob2 = Outbox(tmp_path / "o.json")  # restart
    api = FakeApi()
    assert ob2.flush(api) == 2 and api.sent == [(1, "qr"), (2, "pin")] and ob2.items == []
    ob2.add(3, "qr")
    assert ob2.flush(FakeApi("409")) == 1 and ob2.items == []  # refused for good: dropped


def test_local_web_api(setup):
    pm, hw, *_ = setup
    holder = SimHolder()
    server = serve(holder, 0)
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def post(path, body):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            return json.loads(urllib.request.urlopen(req).read())
        except urllib.error.HTTPError as e:
            return json.loads(e.read())

    try:
        assert json.loads(urllib.request.urlopen(base + "/api/status").read())["ready"] is False
        assert post("/api/pin", {"pin": "123456"})["ok"] is False  # still starting
        holder.pickup, holder.kiosk_name = pm, "Demo"
        st = json.loads(urllib.request.urlopen(base + "/api/status").read())
        assert st["ready"] and st["kiosk"] == "Demo" and st["active_codes"] == 3
        r = post("/api/pin", {"pin": "123456"})
        assert r["ok"] and r["slots"] == [1]
        assert json.loads(urllib.request.urlopen(base + "/api/status").read())["open_slots"] == [1]
        assert post("/api/qr", {"code": "PL1:8:tok8"})["slots"] == [2, 3]
        assert b"Scan your QR code" in urllib.request.urlopen(base + "/kiosk").read()
    finally:
        server.shutdown()
