"""Job state machine and the compartment state that follows each job state."""
from __future__ import annotations

from enum import Enum


class JobStatus(str, Enum):
    UPLOADED = "uploaded"
    QUEUED = "queued"
    PRINTING = "printing"
    DELIVERING = "delivering"
    READY = "ready"
    COLLECTED = "collected"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class CompartmentState(str, Enum):
    FREE = "free"
    RESERVED = "reserved"
    OCCUPIED = "occupied"
    EXPIRED = "expired"  # holds uncollected paper until an operator empties it
    FAULT = "fault"  # needs an operator check (e.g. delivery failed half-way)


TERMINAL: frozenset[JobStatus] = frozenset(
    {JobStatus.COLLECTED, JobStatus.FAILED, JobStatus.EXPIRED, JobStatus.CANCELLED}
)

# Jobs in these states count as "active" (they own compartments / codes).
ACTIVE: frozenset[JobStatus] = frozenset(
    {JobStatus.QUEUED, JobStatus.PRINTING, JobStatus.DELIVERING, JobStatus.READY}
)

ALLOWED: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.UPLOADED: frozenset({JobStatus.QUEUED, JobStatus.CANCELLED, JobStatus.FAILED}),
    JobStatus.QUEUED: frozenset({JobStatus.PRINTING, JobStatus.CANCELLED, JobStatus.FAILED}),
    JobStatus.PRINTING: frozenset({JobStatus.DELIVERING, JobStatus.FAILED}),
    JobStatus.DELIVERING: frozenset({JobStatus.READY, JobStatus.FAILED}),
    JobStatus.READY: frozenset({JobStatus.COLLECTED, JobStatus.EXPIRED}),
    JobStatus.COLLECTED: frozenset(),
    JobStatus.FAILED: frozenset(),
    # A kiosk that was offline may report a pickup after the server already expired the job.
    JobStatus.EXPIRED: frozenset({JobStatus.COLLECTED}),
    JobStatus.CANCELLED: frozenset(),
}

USER_CANCELLABLE: frozenset[JobStatus] = frozenset({JobStatus.UPLOADED, JobStatus.QUEUED})


class InvalidTransition(Exception):
    def __init__(self, current: JobStatus, target: JobStatus):
        super().__init__(f"cannot move job from '{current.value}' to '{target.value}'")
        self.current = current
        self.target = target


def can_transition(current: JobStatus, target: JobStatus) -> bool:
    return target in ALLOWED[current]


def check_transition(current: JobStatus, target: JobStatus) -> None:
    if not can_transition(current, target):
        raise InvalidTransition(current, target)


def compartment_state_after(current: JobStatus, target: JobStatus) -> CompartmentState | None:
    """Return the new state for the job's compartments, or None if unchanged.

    Rules (see spec, "Job states"):
    - queued: compartments are reserved
    - ready: paper is inside, compartments occupied
    - collected: freed
    - cancelled, or failed before paper reached the elevator: freed
    - failed while delivering: paper may be stuck, mark fault for an operator
    - expired: codes stop working, paper stays until an operator empties it
    """
    check_transition(current, target)
    if target is JobStatus.QUEUED:
        return CompartmentState.RESERVED
    if target is JobStatus.READY:
        return CompartmentState.OCCUPIED
    if target is JobStatus.COLLECTED:
        return CompartmentState.FREE
    if target is JobStatus.CANCELLED:
        return CompartmentState.FREE
    if target is JobStatus.FAILED:
        return CompartmentState.FAULT if current is JobStatus.DELIVERING else CompartmentState.FREE
    if target is JobStatus.EXPIRED:
        return CompartmentState.EXPIRED
    return None
