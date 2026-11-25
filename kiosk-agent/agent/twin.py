"""Digital twin of the kiosk controller: the ESP32 firmware's behaviour plus the mechanics it drives.

The twin speaks the same JSON-lines protocol as the real controller (see `SerialHardware`), so the agent's
real serial code runs against it unchanged. It is also the executable spec for the firmware (M8): the
firmware is done when `tests/test_controller_protocol.py` passes against the real board.

Geometry and timings come from the hardware design (hardware/fusion/build_kiosk.py) and the motor checks
(hardware/simulation/calc_mechanics.py). Every motion takes its real duration, scaled by `speed`
(1 = real time, 0 = instant for tests); `seconds` in the replies is always the real-machine time.

Protocol (one JSON object per line):
  agent -> controller
    {"id": 1, "cmd": "config", "locks": [{"lock": 1, "column": 1, "slot": 1}, ...]}
    {"id": 2, "cmd": "deliver", "column": 1, "slot": 5, "sheets": 120}
    {"id": 3, "cmd": "unlock", "lock": 4}
    {"id": 4, "cmd": "sensors"}
    {"id": 5, "cmd": "status"}
    {"id": 6, "cmd": "clear", "column": 1}
  controller -> agent
    {"id": 2, "ok": true, "accepted": true}                          deliver starts in the background
    {"event": "delivered", "ref": 2, "seconds": 26.5}                ...and finishes later
    {"event": "delivery_failed", "ref": 2, "error": "pusher stalled at 140 mm"}
    {"id": 4, "ok": true, "sensors": [{"lock": 4, "door_closed": true, "paper": false}, ...]}
    {"event": "step", "column": 1, "step": "lift_up", "from_mm": 346, "to_mm": 876, "seconds": 7.0}
    {"id": n, "ok": false, "error": "..."}
Deliver is asynchronous so that unlock/sensors stay instant while a 30-second delivery runs.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger("agent.twin")


@dataclass(frozen=True)
class Geometry:
    """Millimetres and seconds, from the hardware design."""
    receive_mm: float = 346       # platform top when catching paper under the PT-320 exit
    first_floor_mm: float = 516   # compartment 1 floor; the others follow every pitch_mm
    pitch_mm: float = 90
    push_mm: float = 320          # pusher stroke: platform -> fully inside the compartment
    lift_speed: float = 80        # mm/s = 600 rpm x 8 mm lead
    push_speed: float = 100       # mm/s
    ramp_s: float = 0.4           # accel + decel per move
    settle_s: float = 5.0         # "no new sheet for 5 s" = printer finished
    unlock_pulse_s: float = 0.5
    door_wait_s: float = 60.0     # how long a delivery waits for an open door before failing

    def floor(self, slot: int) -> float:
        return self.first_floor_mm + (slot - 1) * self.pitch_mm

    def move_s(self, dist_mm: float, speed: float) -> float:
        return 0.0 if dist_mm == 0 else abs(dist_mm) / speed + self.ramp_s


class Fault(Exception):
    pass


FAULTS = {
    "lift_stall": "lift stalled (StallGuard) at {mm:.0f} mm",
    "pusher_stall": "pusher stalled (StallGuard) at {mm:.0f} mm",
    "power_cut": "power lost during delivery: lift fell to the bottom stop, paper still on the platform",
    "stack_missing": "compartment sensor sees no paper after the push",
}


@dataclass
class Column:
    no: int
    lift_mm: float
    pusher_mm: float = 0.0
    platform_sheets: int = 0
    phase: str = "idle"
    busy: bool = False
    error: str | None = None
    hold: bool = False            # drivers enabled (T8x8 screws back-drive, so the lift is held while up)


@dataclass
class Lock:
    no: int
    column: int
    slot: int
    locked: bool = True
    door_closed: bool = True
    sheets: int = 0


@dataclass
class ControllerTwin:
    geometry: Geometry = field(default_factory=Geometry)
    speed: float = 1.0
    out: callable = print                       # where reply/event lines go

    def __post_init__(self):
        self.cols: dict[int, Column] = {}
        self.locks: dict[int, Lock] = {}
        self.fault_next: str | None = None
        self.mx = threading.RLock()

    # ------------------------------------------------------------ physical world (not protocol)
    def sheet(self, column: int) -> None:
        """A sheet landed on the platform (the platform IR sensor counts it)."""
        with self.mx:
            self._col(column).platform_sheets += 1

    def user_opens(self, lock: int) -> None:
        with self.mx:
            self.locks[lock].door_closed = False

    def user_closes(self, lock: int, take_paper: bool = False) -> None:
        with self.mx:
            lk = self.locks[lock]
            if take_paper:
                lk.sheets = 0
            lk.door_closed, lk.locked = True, True   # spring latch catches when the door shuts
        self._emit({"event": "door", "lock": lock, "door_closed": True})

    # ------------------------------------------------------------ protocol
    def feed(self, line: str) -> None:
        try:
            msg = json.loads(line)
        except ValueError:
            return
        mid = msg.get("id")
        try:
            reply = self._handle(msg)
            self._emit({"id": mid, "ok": True, **reply})
        except Fault as exc:
            self._emit({"id": mid, "ok": False, "error": str(exc)})
        except (KeyError, TypeError, ValueError) as exc:
            self._emit({"id": mid, "ok": False, "error": f"bad command: {exc}"})

    def _handle(self, m: dict) -> dict:
        cmd = m.get("cmd")
        if cmd == "config":
            with self.mx:
                self.locks = {int(x["lock"]): Lock(int(x["lock"]), int(x["column"]), int(x["slot"]))
                              for x in m["locks"]}
                for c in {lk.column for lk in self.locks.values()}:
                    self.cols.setdefault(c, Column(c, self.geometry.receive_mm))
            return {"locks": len(self.locks)}
        if cmd == "deliver":
            col, slot, sheets = int(m["column"]), int(m["slot"]), int(m["sheets"])
            with self.mx:
                c = self._col(col)
                if c.busy:
                    raise Fault(f"column {col} is busy")
                if c.error:
                    raise Fault(f"column {col} needs an operator: {c.error}")
                self._lock_at(col, slot)
                c.busy = True
            threading.Thread(target=self._deliver, args=(m.get("id"), col, slot, sheets),
                             name=f"twin-col{col}", daemon=True).start()
            return {"accepted": True}
        if cmd == "unlock":
            lk = self.locks[int(m["lock"])]
            with self.mx:
                lk.locked = False
            self._emit({"event": "lock_pulse", "lock": lk.no, "seconds": self.geometry.unlock_pulse_s})
            self.user_opens(lk.no)        # simulated user pulls the door open as soon as it releases
            return {}
        if cmd == "sensors":
            with self.mx:
                return {"sensors": [{"lock": lk.no, "door_closed": lk.door_closed, "paper": lk.sheets > 0}
                                    for lk in self.locks.values()]}
        if cmd == "status":
            with self.mx:
                return {"columns": [vars(c).copy() for c in self.cols.values()]}
        if cmd == "clear":
            with self.mx:
                c = self._col(int(m["column"]))
                if c.busy:
                    raise Fault("column is moving")
                c.error, c.platform_sheets, c.pusher_mm, c.phase = None, 0, 0.0, "idle"
                c.lift_mm, c.hold = self.geometry.receive_mm, False
            return {}
        raise Fault(f"unknown command {cmd!r}")

    # ------------------------------------------------------------ firmware: delivery sequence
    def _deliver(self, ref, col: int, slot: int, sheets: int) -> None:
        g, c = self.geometry, self.cols[col]
        total = 0.0
        try:
            total += self._wait("settle", g.settle_s, c)
            with self.mx:
                counted = c.platform_sheets
                if counted == 0:          # driven without a sheet counter (real printer path in tests)
                    c.platform_sheets = counted = sheets
            if counted != sheets:
                raise Fault(f"platform counted {counted} sheets, expected {sheets}")
            total += self._move(c, "lift_up", "lift_mm", g.floor(slot), g.lift_speed, "lift_stall")
            total += self._move(c, "push", "pusher_mm", g.push_mm, g.push_speed, "pusher_stall")
            with self.mx:
                lk = self._lock_at(col, slot)
                if self._take_fault("stack_missing"):
                    raise Fault(FAULTS["stack_missing"])
                lk.sheets += c.platform_sheets
                c.platform_sheets = 0
            total += self._move(c, "pusher_home", "pusher_mm", 0, g.push_speed)
            total += self._move(c, "lift_down", "lift_mm", g.receive_mm, g.lift_speed)
            with self.mx:
                c.phase, c.busy, c.hold = "idle", False, False
            self._emit({"event": "delivered", "ref": ref, "column": col, "slot": slot, "sheets": sheets,
                        "seconds": round(total, 2)})
        except Fault as exc:
            with self.mx:
                c.error, c.busy, c.phase = str(exc), False, "error"
            self._emit({"event": "delivery_failed", "ref": ref, "column": col, "error": str(exc)})

    def _move(self, c: Column, step: str, axis: str, target: float, speed: float, stall: str | None = None) -> float:
        g = self.geometry
        start = getattr(c, axis)
        seconds = g.move_s(target - start, speed)
        self._emit({"event": "step", "column": c.no, "step": step, "from_mm": round(start), "to_mm": round(target),
                    "seconds": round(seconds, 2)})
        with self.mx:
            c.phase, c.hold = step, True
        ticks = max(1, int(seconds * 10)) if self.speed else 1
        fail_at = None
        if stall and self.fault_next == stall:
            fail_at = ticks // 2
        if axis == "lift_mm" and self.fault_next == "power_cut" and step == "lift_up":
            fail_at = ticks // 2
        for i in range(1, ticks + 1):
            self._door_interlock(c)
            if self.speed:
                time.sleep(seconds / ticks * self.speed)
            with self.mx:
                setattr(c, axis, start + (target - start) * i / ticks)
            if fail_at is not None and i >= max(1, fail_at):
                kind = self.fault_next
                self.fault_next = None
                if kind == "power_cut":
                    with self.mx:              # T8x8 back-drives: the unpowered carriage slides down
                        c.lift_mm, c.hold = g.receive_mm, False
                raise Fault(FAULTS[kind].format(mm=getattr(c, axis)))
        return seconds

    def _wait(self, step: str, seconds: float, c: Column) -> float:
        self._emit({"event": "step", "column": c.no, "step": step, "seconds": seconds})
        with self.mx:
            c.phase = step
        if self.speed:
            time.sleep(seconds * self.speed)
        return seconds

    def _door_interlock(self, c: Column) -> None:
        """The lift and pusher never move while any door of this column is open."""
        waited = 0.0
        while True:
            with self.mx:
                open_ = [lk.no for lk in self.locks.values() if lk.column == c.no and not lk.door_closed]
            if not open_:
                if waited:
                    self._emit({"event": "resumed", "column": c.no})
                return
            if waited == 0:
                self._emit({"event": "paused", "column": c.no, "reason": f"door {open_[0]} open"})
            limit = self.geometry.door_wait_s * self.speed
            if waited >= limit:
                raise Fault(f"door {open_[0]} stayed open; delivery stopped")
            time.sleep(0.05)
            waited += 0.05

    # ------------------------------------------------------------ helpers
    def _take_fault(self, kind: str) -> bool:
        if self.fault_next == kind:
            self.fault_next = None
            return True
        return False

    def _col(self, no: int) -> Column:
        if no not in self.cols:
            raise Fault(f"no column {no}")
        return self.cols[no]

    def _lock_at(self, col: int, slot: int) -> Lock:
        for lk in self.locks.values():
            if lk.column == col and lk.slot == slot:
                return lk
        raise Fault(f"no compartment {slot} in column {col}")

    def _emit(self, msg: dict) -> None:
        ev = msg.get("event")
        if ev == "step":
            detail = (f"{msg['from_mm']}→{msg['to_mm']} mm in {msg['seconds']} s" if "to_mm" in msg
                      else f"{msg['seconds']} s")
            log.info("controller: column %s %s, %s", msg["column"], msg["step"], detail)
        elif ev == "delivered":
            log.info("controller: column %s delivered %s sheets to compartment %s (%.1f s machine time)",
                     msg["column"], msg["sheets"], msg["slot"], msg["seconds"])
        elif ev == "delivery_failed":
            log.error("controller: column %s delivery failed: %s", msg["column"], msg["error"])
        elif ev:
            log.info("controller: %s %s", ev, {k: v for k, v in msg.items() if k != "event"})
        self.out(json.dumps(msg))


class TwinLink:
    """A serial-port lookalike (write/readline) connected to an in-process twin."""

    def __init__(self, twin: ControllerTwin):
        import queue
        self._q: queue.Queue[str] = queue.Queue()
        self._empty = queue.Empty
        self.twin = twin
        twin.out = self._q.put

    def write(self, data: bytes) -> int:
        for line in data.decode().splitlines():
            if line.strip():
                self.twin.feed(line)
        return len(data)

    def readline(self) -> bytes:
        try:
            return (self._q.get(timeout=0.5) + "\n").encode()
        except self._empty:
            return b""
