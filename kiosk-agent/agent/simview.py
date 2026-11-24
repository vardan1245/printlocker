"""Small web page that shows the simulated kiosk live (http://localhost:8100).

GET  /            the page
GET  /state       JSON: printers, columns, compartments, recent log lines
POST /action      JSON {"type": "...", ...}: jam a printer, fail a delivery, take paper
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

PAGE = Path(__file__).with_name("simview.html")


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
        elif t == "toggle_paper":
            p = self.printers[int(a["printer_id"])]
            p.paper_ok = not p.paper_ok
        elif t == "clear_fault":
            pid = int(a["printer_id"])
            self.printers[pid].clear_block()
            self.hw.clear_column(pid)
        elif t == "take_paper":
            self.hw.user_takes_paper_and_closes(int(a["compartment_id"]))
        else:
            return {"ok": False, "error": f"unknown action {t!r}"}
        logging.getLogger("agent.sim").info("simulator action: %s", t)
        return {"ok": True}


def serve(sim: SimState, port: int) -> ThreadingHTTPServer:
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

        def do_GET(self):
            if self.path == "/":
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/state":
                self._send(200, json.dumps(sim.state()).encode(), "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path != "/action":
                return self._send(404, b"not found", "text/plain")
            try:
                n = int(self.headers.get("Content-Length", "0"))
                result = sim.action(json.loads(self.rfile.read(n) or b"{}"))
            except (ValueError, KeyError) as exc:
                result = {"ok": False, "error": str(exc)}
            self._send(200 if result.get("ok") else 400, json.dumps(result).encode(), "application/json")

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, name="simview", daemon=True).start()
    return server
