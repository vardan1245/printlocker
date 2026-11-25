"""Serve the controller twin on a pseudo-terminal, so the agent runs in real serial mode against it:

    python -m agent.twin_pty            # prints e.g. "controller twin on /dev/pts/5"
    PL_HARDWARE_MODE=serial PL_SERIAL_PORT=/dev/pts/5 python -m agent.main

This exercises exactly the code path the real ESP32 will use (pyserial, line framing, async replies).
Linux and macOS only.
"""
from __future__ import annotations

import argparse
import logging
import os
import threading
import tty

from .twin import ControllerTwin


def serve(speed: float = 1.0) -> tuple[str, ControllerTwin, threading.Thread]:
    master, slave = os.openpty()
    tty.setraw(slave)
    out_lock = threading.Lock()

    def out(line: str) -> None:
        with out_lock:
            os.write(master, (line + "\n").encode())

    twin = ControllerTwin(speed=speed, out=out)

    def loop() -> None:
        buf = b""
        while True:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                twin.feed(line.decode(errors="replace"))

    t = threading.Thread(target=loop, name="twin-pty", daemon=True)
    t.start()
    return os.ttyname(slave), twin, t


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--speed", type=float, default=1.0, help="1 = real time, 0.1 = ten times faster")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    path, _twin, t = serve(args.speed)
    print(f"controller twin on {path}  (PL_HARDWARE_MODE=serial PL_SERIAL_PORT={path})", flush=True)
    t.join()


if __name__ == "__main__":
    main()
