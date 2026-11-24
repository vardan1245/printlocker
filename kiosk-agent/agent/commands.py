"""Operator commands from the admin panel: open a compartment, clear a blocked printer."""
from __future__ import annotations

import logging
import threading

from .api import ApiClient, ApiError, OfflineError
from .hardware import Hardware, HardwareError, SimHardware
from .printers import PrinterDriver

log = logging.getLogger("agent.commands")


def run_command(cmd: dict, hw: Hardware, printers: dict[int, PrinterDriver]) -> tuple[bool, str | None]:
    """Carry out one command. Returns (ok, error)."""
    t, p = cmd.get("type"), cmd.get("payload") or {}
    try:
        if t == "unlock":
            hw.unlock(int(p["compartment_id"]))
            log.warning("operator opened compartment #%s", p.get("slot_no", p["compartment_id"]))
            return True, None
        if t == "clear_printer":
            pid = int(p["printer_id"])
            drv = printers.get(pid)
            if drv is None:
                return False, f"printer {pid} is not on this kiosk"
            drv.clear_block()
            if isinstance(hw, SimHardware):
                hw.clear_column(pid)
            log.warning("operator cleared printer %s; it takes jobs again", pid)
            return True, None
        return False, f"unknown command {t!r}"
    except (KeyError, ValueError) as exc:
        return False, f"bad command: {exc}"
    except HardwareError as exc:
        return False, str(exc)


def command_loop(api: ApiClient, hw: Hardware, printers: dict[int, PrinterDriver], stop: threading.Event,
                 every: float = 3.0) -> None:
    while not stop.is_set():
        try:
            for cmd in api.commands():
                ok, err = run_command(cmd, hw, printers)
                api.command_done(cmd["id"], ok, err)
        except (OfflineError, ApiError) as exc:
            log.debug("command poll failed: %s", exc)
        except Exception:
            log.exception("command loop error")
        stop.wait(every)
