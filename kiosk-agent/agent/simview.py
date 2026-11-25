"""The agent's local web server.

On the real kiosk, Chromium in kiosk mode shows http://localhost:<port>/kiosk on the
7" touchscreen. In simulator mode, http://localhost:<port>/ shows the whole machine
with the kiosk screen built in.

GET  /                 simulator page (sim mode) or redirect to /kiosk
GET  /kiosk            touchscreen page
GET  /state            simulator JSON
POST /action           simulator actions (jam, fail delivery, take paper, ...)
GET  /api/status       kiosk screen status (open doors, PIN lock, online)
POST /api/qr           {"code": "PL1:..."}  -> open compartment(s)
POST /api/pin          {"pin": "123456"}    -> open compartment(s)
"""
from __future__ import annotations

import collections
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .hardware import SimHardware
from .printers import SimPrinter

SIM_PAGE = Path(__file__).with_name("simview.html")
KIOSK_PAGE = Path(__file__).with_name("kiosk.html")


class LogBuffer(logging.Handler):
    def __init__(self, size: int = 60):
        super().__init__()
        self.lines: collections.deque[str] = collections.deque(maxlen=size)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


class SimState:
    def __init__(self, hw: SimHardware, printers: dict[int, SimPrinter], names: dict[int, str],
                 logs: LogBuffer, kiosk_name: str = ""):
        self.hw, self.printers, self.names, self.logs = hw, printers, names, logs
        self.kiosk_name = kiosk_name

    def state(self) -> dict:
        snap = self.hw.snapshot()
        return {
            "kiosk": self.kiosk_name,
            "printers": [{"id": pid, "name": self.names.get(pid, str(pid)), **p.state,
                          "paper_ok": p.paper_ok, "toner_ok": p.toner_ok, "blocked": p.blocked}
                         for pid, p in self.printers.items()],
            **snap,
            "log": list(self.logs.lines)[-25:],
        }

    def action(self, a: dict) -> dict:
        t = a.get("type")
        if t == "jam_printer":
            self.printers[int(a["printer_id"])].fail_next = "paper jam (simulated)"
        elif t == "fail_delivery":
            self.hw.fail_next_delivery = "elevator stalled (simulated)"
        elif t == "fault" and hasattr(self.hw, "inject_fault"):
            self.hw.inject_fault(str(a["kind"]))  # twin: lift_stall, pusher_stall, power_cut, stack_missing
        elif t == "toggle_paper":
            p = self.printers[int(a["printer_id"])]
            p.paper_ok = not p.paper_ok
        elif t == "clear_fault":
            pid = int(a["printer_id"])
            self.printers[pid].clear_block()
            self.hw.clear_column(pid)
        elif t == "take_paper":
            self.hw.user_takes_paper_and_closes(int(a["compartment_id"]))
        elif t == "close_door":
            self.hw.close_door(int(a["compartment_id"]))
        else:
            return {"ok": False, "error": f"unknown action {t!r}"}
        logging.getLogger("agent.sim").info("simulator action: %s", t)
        return {"ok": True}


class SimHolder:
    """Filled in once the agent has its configuration from the server.

    sim:    SimState (simulator mode only)
    pickup: PickupManager
    online: callable returning whether the last heartbeat reached the server
    """

    def __init__(self, sim: SimState | None = None):
        self.sim = sim
        self.pickup = None
        self.online = lambda: True
        self.kiosk_name = ""


def serve(sim: SimState | SimHolder, port: int) -> ThreadingHTTPServer:
    holder = sim if isinstance(sim, SimHolder) else SimHolder(sim)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the console clean
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj).encode(), "application/json")

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(n) or b"{}")

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/":
                if holder.sim is None and holder.pickup is not None:  # real kiosk: screen only
                    self.send_response(302)
                    self.send_header("Location", "/kiosk")
                    self.end_headers()
                    return
                self._send(200, SIM_PAGE.read_bytes(), "text/html; charset=utf-8")
            elif path == "/kiosk":
                self._send(200, KIOSK_PAGE.read_bytes(), "text/html; charset=utf-8")
            elif path == "/state":
                self._json(200, holder.sim.state() if holder.sim else {"connecting": True})
            elif path == "/api/status":
                if holder.pickup is None:
                    return self._json(200, {"ready": False, "online": False})
                self._json(200, {"ready": True, "online": bool(holder.online()), "kiosk": holder.kiosk_name,
                                 **holder.pickup.status()})
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            path = self.path.split("?")[0]
            try:
                body = self._body()
            except ValueError:
                return self._json(400, {"ok": False, "error": "bad JSON"})
            if path == "/action":
                if holder.sim is None:
                    return self._json(503, {"ok": False, "error": "connecting"})
                try:
                    result = holder.sim.action(body)
                except (ValueError, KeyError) as exc:
                    result = {"ok": False, "error": str(exc)}
                return self._json(200 if result.get("ok") else 400, result)
            if path in ("/api/qr", "/api/pin"):
                if holder.pickup is None:
                    return self._json(503, {"ok": False, "message": "The kiosk is starting, please wait."})
                if path == "/api/qr":
                    r = holder.pickup.scan_qr(str(body.get("code", "")))
                else:
                    r = holder.pickup.enter_pin(str(body.get("pin", "")))
                return self._json(200, r.as_dict())
            self._send(404, b"not found", "text/plain")

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, name="localweb", daemon=True).start()
    return server
