from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(v: str | None, default: bool = False) -> bool:
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AgentConfig:
    server_url: str  # e.g. http://localhost:8000/api/v1
    kiosk_key: str
    printer_mode: str = "sim"  # sim | cups
    hardware_mode: str = "twin"  # twin (controller twin) | sim (instant stand-in) | serial (real ESP32)
    serial_port: str = "/dev/ttyUSB0"
    sim_speed: float = 1.0  # 1 = realistic-ish timings, 0 = instant (tests)
    sim_view_port: int = 8100  # 0 disables the simulator web page
    poll_seconds: float = 2.0
    heartbeat_seconds: float = 15.0
    data_dir: str = "./agent-data"
    cups_options: tuple[str, ...] = ("outputorder=reverse",)

    @classmethod
    def from_env(cls) -> AgentConfig:
        e = os.environ
        url = e.get("PL_SERVER_URL", "http://localhost:8000/api/v1").rstrip("/")
        key = e.get("PL_KIOSK_KEY", "")
        if not key:
            raise SystemExit("PL_KIOSK_KEY is not set (the key shown when the kiosk was created)")
        return cls(
            server_url=url,
            kiosk_key=key,
            printer_mode=e.get("PL_PRINTER_MODE", "sim"),
            hardware_mode=e.get("PL_HARDWARE_MODE", "twin"),
            serial_port=e.get("PL_SERIAL_PORT", "/dev/ttyUSB0"),
            sim_speed=float(e.get("PL_SIM_SPEED", "1.0")),
            sim_view_port=int(e.get("PL_SIM_VIEW_PORT") or e.get("PORT") or "8100"),
            poll_seconds=float(e.get("PL_POLL_SECONDS", "2")),
            heartbeat_seconds=float(e.get("PL_HEARTBEAT_SECONDS", "15")),
            data_dir=e.get("PL_DATA_DIR", "./agent-data"),
            # space-separated lp -o options, e.g. "outputorder=reverse OutputBin=FaceUp"
            cups_options=tuple(e.get("PL_CUPS_OPTIONS", "outputorder=reverse").split()),
        )
