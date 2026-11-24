"""Start the kiosk agent:  python -m agent.main   (settings come from PL_* environment variables)."""
from __future__ import annotations

import logging
import signal
import threading
import time
from pathlib import Path

from .api import ApiClient, ApiError, OfflineError
from .config import AgentConfig
from .hardware import Hardware, SerialHardware, SimHardware
from .pickup import CodeCache, Outbox, PickupManager
from .printers import CupsPrinter, PrinterDriver, SimPrinter
from .simview import LogBuffer, SimHolder, SimState, serve
from .worker import PrinterWorker

log = logging.getLogger("agent")


def fetch_config(api: ApiClient, stop: threading.Event) -> dict:
    """Wait until the server answers; a kiosk may boot before the network is up."""
    while not stop.is_set():
        try:
            return api.config()
        except OfflineError as exc:
            log.warning("server unreachable (%s), retrying in 5s", exc)
        except ApiError as exc:
            if exc.status == 401:
                raise SystemExit("server rejected PL_KIOSK_KEY") from None
            log.warning("config failed: %s", exc)
        stop.wait(5)
    raise SystemExit(0)


class Status:
    """Shared flags for the kiosk screen."""

    def __init__(self):
        self.online = False


def heartbeat_loop(api: ApiClient, printers: list[PrinterDriver], hw: Hardware, every: float,
                   stop: threading.Event, status: Status) -> None:
    while not stop.is_set():
        try:
            api.heartbeat([p.report() for p in printers], hw.sensors())
            status.online = True
        except (OfflineError, ApiError) as exc:
            status.online = False
            log.warning("heartbeat failed: %s", exc)
        except Exception:
            log.exception("heartbeat error")
        stop.wait(every)


def pickup_loop(api: ApiClient, pickup: PickupManager, cache: CodeCache, outbox: Outbox,
                stop: threading.Event, refresh_every: float = 10.0) -> None:
    """Watch open doors, send pickup reports, keep the offline code list fresh."""
    last_refresh = 0.0
    while not stop.is_set():
        try:
            if pickup.check_doors() or (outbox.items and int(time.time()) % 5 == 0):
                outbox.flush(api)
            if time.time() - last_refresh > refresh_every:
                cache.refresh(api)  # codes used here offline stay blocked (CodeCache.used)
                last_refresh = time.time()
        except Exception:
            log.exception("pickup loop error")
        stop.wait(0.5)


def main() -> None:
    logbuf = LogBuffer()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger().addHandler(logbuf)

    cfg = AgentConfig.from_env()
    data = Path(cfg.data_dir)
    data.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    api = ApiClient(cfg.server_url, cfg.kiosk_key)
    holder = SimHolder()
    if cfg.sim_view_port:
        serve(holder, cfg.sim_view_port)  # page is up at once; shows "connecting" until configured
        log.info("local page: http://localhost:%d (kiosk screen: /kiosk)", cfg.sim_view_port)
    log.info("connecting to %s", cfg.server_url)
    conf = fetch_config(api, stop)
    log.info("kiosk %s (%s): %d printer(s), %d compartment(s)", conf["kiosk_id"], conf["name"],
             len(conf["printers"]), len(conf["compartments"]))

    printer_ids = [p["id"] for p in conf["printers"]]
    hw: Hardware = SimHardware(cfg.sim_speed) if cfg.hardware_mode == "sim" else SerialHardware(cfg.serial_port)
    hw.setup(conf["compartments"], printer_ids)

    printers: dict[int, PrinterDriver] = {}
    for p in conf["printers"]:
        if cfg.printer_mode == "sim":
            printers[p["id"]] = SimPrinter(p["id"], p["cups_queue"], cfg.sim_speed,
                                           on_sheet=hw.add_sheet if isinstance(hw, SimHardware) else None)
        else:
            printers[p["id"]] = CupsPrinter(p["id"], p["cups_queue"])

    # Jobs that were mid-print when the agent last stopped can't be trusted: the server fails
    # them, and their printer columns stay blocked until an operator checks for loose paper.
    try:
        for j in api.recover().get("failed_jobs", []):
            drv = printers.get(j["printer_id"])
            if drv:
                drv.blocked = f"job {j['id']} was interrupted by a restart"
                log.error("printer %s blocked: job %s was interrupted by a restart", j["printer_id"], j["id"])
    except (OfflineError, ApiError) as exc:
        log.warning("recovery check failed: %s", exc)

    cache = CodeCache(conf["code_key"], data / "codes.json")
    outbox = Outbox(data / "outbox.json")
    pickup = PickupManager(cache, hw, outbox)
    status = Status()
    if isinstance(hw, SimHardware):
        sims = {pid: d for pid, d in printers.items() if isinstance(d, SimPrinter)}
        names = {p["id"]: p["name"] for p in conf["printers"]}
        holder.sim = SimState(hw, sims, names, logbuf, conf["name"])
    holder.kiosk_name = conf["name"]
    holder.online = lambda: status.online
    holder.pickup = pickup

    outbox.flush(api)
    cache.refresh(api)
    threading.Thread(target=heartbeat_loop, args=(api, list(printers.values()), hw, cfg.heartbeat_seconds,
                                                  stop, status), name="heartbeat", daemon=True).start()
    threading.Thread(target=pickup_loop, args=(api, pickup, cache, outbox, stop), name="pickup",
                     daemon=True).start()
    for drv in printers.values():
        PrinterWorker(api, drv, hw, cfg.poll_seconds, stop, on_ready=lambda _id: cache.refresh(api)).start()

    log.info("agent running (printer=%s, hardware=%s)", cfg.printer_mode, cfg.hardware_mode)
    while not stop.is_set():
        time.sleep(0.5)
    log.info("stopping")


if __name__ == "__main__":
    main()
