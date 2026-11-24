"""Mechanics: elevator + pusher per printer column, a lock and sensors per compartment.

Two implementations with the same interface:
- SimHardware: in memory, with timings, used on a laptop and by the simulator page.
- SerialHardware: talks to the ESP32 firmware over USB serial (JSON lines).
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


class SerialHardware(Hardware):
    """ESP32 protocol, one JSON object per line.

    agent -> esp32: {"id": 7, "cmd": "deliver", "column": 1, "slot": 3, "sheets": 120}
                    {"id": 8, "cmd": "unlock", "lock": 4}
                    {"id": 9, "cmd": "sensors"}
    esp32 -> agent: {"id": 7, "ok": true} | {"id": 7, "ok": false, "error": "elevator stalled"}
                    {"id": 9, "ok": true, "sensors": [{"lock": 4, "door_closed": true, "paper": false}, ...]}
    `lock` numbers = position of the compartment in the server's compartment list (1-based),
    which matches the wiring order on the MCP23017 expanders.
    """

    def __init__(self, port: str, baud: int = 115200, timeout: float = 180.0):
        import serial  # pyserial; only needed on the real kiosk

        self._ser = serial.Serial(port, baud, timeout=0.5)
        self._timeout = timeout
        self._ids = itertools.count(1)
        self._io = threading.Lock()
        self._lock_no: dict[int, int] = {}
        self._column: dict[int, int] = {}

    def setup(self, compartments: list[dict], printer_ids: list[int]) -> None:
        self._column = {pid: i for i, pid in enumerate(printer_ids, start=1)}
        self._lock_no = {c["id"]: i for i, c in enumerate(compartments, start=1)}

    def _call(self, cmd: dict) -> dict:
        with self._io:
            msg_id = next(self._ids)
            self._ser.write((json.dumps({"id": msg_id, **cmd}) + "\n").encode())
            deadline = time.monotonic() + self._timeout
            while time.monotonic() < deadline:
                line = self._ser.readline().decode(errors="replace").strip()
                if not line:
                    continue
                try:
                    reply = json.loads(line)
                except ValueError:
                    log.debug("esp32: %s", line)  # free-text debug output
                    continue
                if reply.get("id") != msg_id:
                    continue
                if not reply.get("ok"):
                    raise HardwareError(reply.get("error", "unknown hardware error"))
                return reply
            raise HardwareError(f"no answer from controller to {cmd['cmd']}")

    def deliver(self, printer_id: int, slot_no: int, sheets: int) -> None:
        self._call({"cmd": "deliver", "column": self._column[printer_id], "slot": slot_no, "sheets": sheets})

    def unlock(self, compartment_id: int) -> None:
        self._call({"cmd": "unlock", "lock": self._lock_no[compartment_id]})

    def sensors(self) -> list[dict]:
        reply = self._call({"cmd": "sensors"})
        by_lock = {v: k for k, v in self._lock_no.items()}
        return [{"id": by_lock[s["lock"]], "door_closed": s.get("door_closed"),
                 "paper_present": s.get("paper")} for s in reply.get("sensors", []) if s["lock"] in by_lock]
