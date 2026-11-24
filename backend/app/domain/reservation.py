"""Choosing compartments for a job."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .states import CompartmentState


@dataclass(frozen=True)
class CompartmentInfo:
    id: int
    printer_id: int
    slot_no: int
    state: CompartmentState


class NoCompartmentAvailable(Exception):
    def __init__(self, needed: int, free: int):
        super().__init__(f"job needs {needed} compartment(s), only {free} free")
        self.needed = needed
        self.free = free


def free_count(compartments: Iterable[CompartmentInfo], printer_id: int) -> int:
    return sum(
        1 for c in compartments if c.printer_id == printer_id and c.state is CompartmentState.FREE
    )


def choose_compartments(
    compartments: Iterable[CompartmentInfo], printer_id: int, needed: int
) -> list[CompartmentInfo]:
    """Pick `needed` free compartments in the printer's column.

    Lowest slot first (slot 1 = top, closest to the printer), so the elevator
    travels as little as possible. Parts of a split job get consecutive picks.
    """
    if needed < 1:
        raise ValueError("needed must be >= 1")
    free = sorted(
        (c for c in compartments if c.printer_id == printer_id and c.state is CompartmentState.FREE),
        key=lambda c: c.slot_no,
    )
    if len(free) < needed:
        raise NoCompartmentAvailable(needed, len(free))
    return free[:needed]
