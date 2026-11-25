"""Mechanics: elevator + pusher per printer column, a lock and sensors per compartment.

Three implementations with the same interface:
- SimHardware: tiny in-memory stand-in, instant; used by the unit tests.
- SerialHardware: talks to the ESP32 controller over USB serial (JSON lines, protocol in twin.py).
- TwinHardware: SerialHardware connected to the controller twin (agent/twin.py): real geometry,
  timings, sensors, interlocks and faults. Used by the simulator page (PL_HARDWARE_MODE=twin).
"""
from __future__ import annotations

import itertools
import json
import logging
import threading
import time

log = logging.getLogger("agent.hw")


class HardwareError(Exception):
    pass


class Hardware:
    def setup(self, compartments: list[dict], printer_ids: list[int]) -> None:
        """compartments: [{'id', 'printer_id', 'slot_no'}] from the server config."""
        raise NotImplementedError

    def deliver(self, printer_id: int, slot_no: int, sheets: int) -> None:
        """Move the printed stack from the top tray into compartment `slot_no`. Blocks."""
        raise NotImplementedError

    def unlock(self, compartment_id: int) -> None:
        raise NotImplementedError

    def sensors(self) -> list[dict]:
        """[{'id', 'door_closed', 'paper_present'}] for the heartbeat."""
        raise NotImplementedError

    def clear_column(self, printer_id: int) -> None:
        """Operator removed leftover paper: reset the column's error state."""


class SimHardware(Hardware):
    STEP_SECONDS = 0.5  # elevator travel per slot
    PUSH_SECONDS = 0.8

    def __init__(self, speed: float = 1.0):
        self.speed = speed
        self.lock = threading.RLock()
        self.columns: dict[int, dict] = {}
        self.compartments: dict[int, dict] = {}
        self.fail_next_delivery: str | None = None

    def setup(self, compartments: list[dict], printer_ids: list[int]) -> None:
        with self.lock:
            for i, pid in enumerate(printer_ids, start=1):
                self.columns[pid] = {"column": i, "elevator_slot": 0, "tray_sheets": 0,
                                     "pusher": "home", "busy": False, "error": None}
            for c in compartments:
                self.compartments[c["id"]] = {
                    "id": c["id"], "printer_id": c["printer_id"], "slot_no": c["slot_no"],
                    "locked": True, "door_closed": True, "paper_sheets": 0,
                }

    def _sleep(self, seconds: float) -> None:
        if self.speed:
            time.sleep(seconds * self.speed)

    def add_sheet(self, printer_id: int) -> None:
        """Called by the simulated printer for every printed sheet."""
        with self.lock:
            self.columns[printer_id]["tray_sheets"] += 1

    def _comp(self, printer_id: int, slot_no: int) -> dict:
        for c in self.compartments.values():
            if c["printer_id"] == printer_id and c["slot_no"] == slot_no:
                return c
        raise HardwareError(f"no compartment {slot_no} in column of printer {printer_id}")

    def deliver(self, printer_id: int, slot_no: int, sheets: int) -> None:
        col = self.columns[printer_id]
        comp = self._comp(printer_id, slot_no)
        with self.lock:
            col.update(busy=True, error=None)
            if col["tray_sheets"] == 0:  # real printer path (no on_sheet callback)
                col["tray_sheets"] = sheets
        try:
            while col["elevator_slot"] < slot_no:  # down, one slot at a time
                self._sleep(self.STEP_SECONDS)
                col["elevator_slot"] += 1
            if self.fail_next_delivery:
                msg, self.fail_next_delivery = self.fail_next_delivery, None
                col["error"] = msg
                raise HardwareError(msg)
            col["pusher"] = "pushing"
            self._sleep(self.PUSH_SECONDS)
            with self.lock:
                comp["paper_sheets"] += col["tray_sheets"]
                col["tray_sheets"] = 0
            col["pusher"] = "home"
            self._sleep(self.PUSH_SECONDS / 2)
            while col["elevator_slot"] > 0:  # back to the top for the next job
                self._sleep(self.STEP_SECONDS / 2)
                col["elevator_slot"] -= 1
        finally:
            col["busy"] = False

    def unlock(self, compartment_id: int) -> None:
        with self.lock:
            c = self.compartments[compartment_id]
            c["locked"] = False
            c["door_closed"] = False  # the user opens it

    def clear_column(self, printer_id: int) -> None:
        """Simulator action: operator removed leftover paper and reset the elevator."""
        with self.lock:
            self.columns[printer_id].update(tray_sheets=0, elevator_slot=0, pusher="home", error=None, busy=False)

    def close_door(self, compartment_id: int) -> None:
        """Simulator action: door shut without taking the paper."""
        with self.lock:
            self.compartments[compartment_id].update(door_closed=True, locked=True)

    def user_takes_paper_and_closes(self, compartment_id: int) -> None:
        """Simulator action: the user empties the compartment and shuts the door."""
        with self.lock:
            c = self.compartments[compartment_id]
            c.update(paper_sheets=0, door_closed=True, locked=True)

    def sensors(self) -> list[dict]:
        with self.lock:
            return [{"id": c["id"], "door_closed": c["door_closed"],
                     "paper_present": c["paper_sheets"] > 0} for c in self.compartments.values()]

    def snapshot(self) -> dict:
        with self.lock:
            return {"columns": {str(k): dict(v) for k, v in self.columns.items()},
                    "compartments": [dict(c) for c in self.compartments.values()]}


class _Waiter:
    def __init__(self):
        self.ev, self.msg = threading.Event(), None

    def set(self, msg: dict) -> None:
        self.msg = msg
        self.ev.set()

    def wait(self, timeout: float) -> dict | None:
        return self.msg if self.ev.wait(timeout) else None


class SerialHardware(Hardware):
    """The controller (ESP32 firmware, or its twin in agent/twin.py) over a serial line, JSON lines.

    The full protocol is documented in agent/twin.py. Short version:
      -> {"id": 2, "cmd": "deliver", "column": 1, "slot": 3, "sheets": 120}
      <- {"id": 2, "ok": true, "accepted": true}            at once
      <- {"event": "delivered", "ref": 2, "seconds": 22.1}  when the stack is in the compartment
    Deliver is asynchronous, so unlock and sensors answer at once even while a delivery runs.
    `lock` numbers = position of the compartment in the server's compartment list (1-based), which
    matches the wiring order on the MCP23017 expanders; `config` tells the controller the layout.
    """

    REPLY_TIMEOUT = 5.0

    def __init__(self, port: str | None = None, baud: int = 115200, timeout: float = 180.0, transport=None):
        if transport is None:
            import serial  # pyserial; only needed on the real kiosk

            transport = serial.Serial(port, baud, timeout=0.5)
        self._ser = transport
        self._timeout = timeout
        self._ids = itertools.count(1)
        self._write = threading.Lock()
        self._pending: dict[int, _Waiter] = {}
        self._finals: dict[int, _Waiter] = {}
        self._lock_no: dict[int, int] = {}
        self._column: dict[int, int] = {}
        threading.Thread(target=self._read_loop, name="controller-rx", daemon=True).start()

    def setup(self, compartments: list[dict], printer_ids: list[int]) -> None:
        self._column = {pid: i for i, pid in enumerate(printer_ids, start=1)}
        self._lock_no = {c["id"]: i for i, c in enumerate(compartments, start=1)}
        self._call({"cmd": "config", "locks": [
            {"lock": self._lock_no[c["id"]], "column": self._column[c["printer_id"]], "slot": c["slot_no"]}
            for c in compartments if c["printer_id"] in self._column]})

    # ------------------------------------------------------------ transport
    def _read_loop(self) -> None:
        while True:
            try:
                line = self._ser.readline().decode(errors="replace").strip()
            except Exception as exc:  # port unplugged etc.; keep trying
                log.error("controller link: %s", exc)
                time.sleep(1)
                continue
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                log.debug("controller: %s", line)  # free-text debug output
                continue
            if "id" in msg and msg["id"] in self._pending:
                self._pending.pop(msg["id"]).set(msg)
            elif msg.get("event") in ("delivered", "delivery_failed") and msg.get("ref") in self._finals:
                self._finals.pop(msg["ref"]).set(msg)

    def _call(self, cmd: dict, final: bool = False) -> dict:
        msg_id = next(self._ids)
        w = self._pending[msg_id] = _Waiter()
        f = None
        if final:
            f = self._finals[msg_id] = _Waiter()
        with self._write:
            self._ser.write((json.dumps({"id": msg_id, **cmd}) + "\n").encode())
        reply = w.wait(self.REPLY_TIMEOUT)
        if reply is None:
            self._pending.pop(msg_id, None)
            self._finals.pop(msg_id, None)
            raise HardwareError(f"no answer from controller to {cmd['cmd']}")
        if not reply.get("ok"):
            self._finals.pop(msg_id, None)
            raise HardwareError(reply.get("error", "unknown hardware error"))
        if not final:
            return reply
        done = f.wait(self._timeout)
        if done is None:
            self._finals.pop(msg_id, None)
            raise HardwareError(f"controller did not finish {cmd['cmd']} within {self._timeout:.0f} s")
        if done["event"] == "delivery_failed":
            raise HardwareError(done.get("error", "delivery failed"))
        return done

    # ------------------------------------------------------------ Hardware API
    def deliver(self, printer_id: int, slot_no: int, sheets: int) -> float | None:
        """Blocks until the stack is in the compartment; returns the controller's machine time (s)."""
        done = self._call({"cmd": "deliver", "column": self._column[printer_id], "slot": slot_no,
                           "sheets": sheets}, final=True)
        return done.get("seconds")

    def unlock(self, compartment_id: int) -> None:
        self._call({"cmd": "unlock", "lock": self._lock_no[compartment_id]})

    def clear_column(self, printer_id: int) -> None:
        self._call({"cmd": "clear", "column": self._column[printer_id]})

    def sensors(self) -> list[dict]:
        reply = self._call({"cmd": "sensors"})
        by_lock = {v: k for k, v in self._lock_no.items()}
        return [{"id": by_lock[s["lock"]], "door_closed": s.get("door_closed"),
                 "paper_present": s.get("paper")} for s in reply.get("sensors", []) if s["lock"] in by_lock]

    def status(self) -> dict:
        return self._call({"cmd": "status"})


class TwinHardware(SerialHardware):
    """SerialHardware wired to the in-process controller twin, plus the simulator's "physical world"
    hooks (sheets landing, users at the doors). Used by PL_HARDWARE_MODE=twin and the simulator page."""

    def __init__(self, speed: float = 1.0, timeout: float = 600.0):
        from .twin import ControllerTwin, TwinLink

        self.twin = ControllerTwin(speed=speed)
        self._comps: dict[int, dict] = {}
        super().__init__(transport=TwinLink(self.twin), timeout=timeout)

    def setup(self, compartments: list[dict], printer_ids: list[int]) -> None:
        self._comps = {c["id"]: dict(c) for c in compartments}
        self._printer_of_col = {}
        super().setup(compartments, printer_ids)
        self._printer_of_col = {v: k for k, v in self._column.items()}

    # physical world
    def add_sheet(self, printer_id: int) -> None:
        self.twin.sheet(self._column[printer_id])

    def user_takes_paper_and_closes(self, compartment_id: int) -> None:
        self.twin.user_closes(self._lock_no[compartment_id], take_paper=True)

    def close_door(self, compartment_id: int) -> None:
        self.twin.user_closes(self._lock_no[compartment_id])

    def inject_fault(self, kind: str) -> None:
        from .twin import FAULTS

        if kind not in FAULTS:
            raise HardwareError(f"unknown fault {kind!r}")
        self.twin.fault_next = kind

    @property
    def fail_next_delivery(self) -> str | None:
        return self.twin.fault_next

    @fail_next_delivery.setter
    def fail_next_delivery(self, value: str | None) -> None:
        self.twin.fault_next = "lift_stall" if value else None

    def snapshot(self) -> dict:
        """Same shape as SimHardware.snapshot(), plus mm positions for the twin view."""
        g = self.twin.geometry
        with self.twin.mx:
            cols = {}
            for c in self.twin.cols.values():
                top = g.floor(max((lk.slot for lk in self.twin.locks.values() if lk.column == c.no), default=1))
                cols[str(self._printer_of_col.get(c.no, c.no))] = {
                    "lift_mm": round(c.lift_mm), "pusher_mm": round(c.pusher_mm),
                    "lift_frac": (c.lift_mm - g.receive_mm) / max(1.0, top - g.receive_mm),
                    "push_frac": c.pusher_mm / g.push_mm, "tray_sheets": c.platform_sheets,
                    "phase": c.phase, "busy": c.busy, "error": c.error, "hold": c.hold,
                    "pusher": "pushing" if c.phase == "push" else "home",
                }
            comps = []
            for cid, lock in self._lock_no.items():
                lk = self.twin.locks[lock]
                comps.append({"id": cid, "printer_id": self._comps[cid]["printer_id"], "slot_no": lk.slot,
                              "locked": lk.locked, "door_closed": lk.door_closed, "paper_sheets": lk.sheets})
        geo = {"receive_mm": g.receive_mm, "first_floor_mm": g.first_floor_mm, "pitch_mm": g.pitch_mm,
               "push_mm": g.push_mm}
        return {"columns": cols, "compartments": comps, "twin": True, "geometry": geo}

    @property
    def compartments(self) -> dict[int, dict]:
        return {c["id"]: c for c in self.snapshot()["compartments"]}
