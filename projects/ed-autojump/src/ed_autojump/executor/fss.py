"""
Req 5 — FSS sweep, Path A (keyboard-only) and Path B (CV-assisted stub).

Path A (default per SPEC §9.6.1):
- Enter FSS via ExplorationFSSEnter
- Hold ExplorationFSSRadioTuningX_Decrease from max to min over t_sweep
- Then back up
- Watch for FSSAllBodiesFound or per-body Scan events; exit on either
  completion or per-system timeout

Path B is sketched as an interface; CV implementation deferred pending
in-game calibration.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable, Iterable, Optional, Protocol

from ..journal.events import Event, FSSAllBodiesFound, Scan
from ..keys.sender import Sender


FSS_SWEEP_DURATION_S = 30.0
FSS_PER_SYSTEM_TIMEOUT_S = 90.0


class FssResult(Enum):
    ALL_BODIES = auto()
    TIMEOUT = auto()
    DISABLED = auto()
    NOT_BOUND = auto()


@dataclass
class FssOutcome:
    result: FssResult
    bodies_scanned: int = 0
    scanned_body_names: list[str] | None = None


def perform_fss_keyboard_sweep(
    sender: Sender,
    events: Iterable[Event],
    *,
    enabled: bool = True,
    sweep_duration_s: float = FSS_SWEEP_DURATION_S,
    timeout_s: float = FSS_PER_SYSTEM_TIMEOUT_S,
    clock: Callable[[], float] = lambda: 0.0,
) -> FssOutcome:
    """
    Path A. Press FSS-enter, hold the tuning-decrease key for sweep_duration_s,
    then exit. Watches for FSSAllBodiesFound during the wait window.

    `enabled=False` short-circuits and returns DISABLED. `events` is an
    iterator of journal events.
    """
    if not enabled:
        return FssOutcome(result=FssResult.DISABLED)
    try:
        sender.press("ExplorationFSSEnter", hold=0.05)
    except KeyError:
        return FssOutcome(result=FssResult.NOT_BOUND)
    sender.press("ExplorationFSSRadioTuningX_Decrease", hold=sweep_duration_s)

    deadline = clock() + timeout_s
    scanned: list[str] = []
    for ev in events:
        if isinstance(ev, Scan):
            scanned.append(ev.body_name)
        if isinstance(ev, FSSAllBodiesFound):
            sender.press("ExplorationFSSQuit", hold=0.05)
            return FssOutcome(
                result=FssResult.ALL_BODIES,
                bodies_scanned=len(scanned),
                scanned_body_names=scanned,
            )
        if clock() >= deadline:
            break

    sender.press("ExplorationFSSQuit", hold=0.05)
    return FssOutcome(
        result=FssResult.TIMEOUT,
        bodies_scanned=len(scanned),
        scanned_body_names=scanned,
    )


class CvBlobSource(Protocol):
    """
    Interface for the CV-assisted FSS path (SPEC §9.6.1 Path B).
    Implementations capture FSS HUD region + detect signal blob centroids.
    """

    def detect_blobs(self) -> list[tuple[float, float]]:  # noqa: D401
        ...


def perform_fss_cv_assisted(
    sender: Sender,
    events: Iterable[Event],
    blob_source: CvBlobSource,
    *,
    enabled: bool = True,
    timeout_s: float = FSS_PER_SYSTEM_TIMEOUT_S,
    clock: Callable[[], float] = lambda: 0.0,
) -> FssOutcome:
    """
    Path B. Calls `blob_source.detect_blobs()`, tunes toward each candidate,
    fires ExplorationFSSTarget to resolve. Stops on FSSAllBodiesFound or
    timeout.

    The blob_source is fully injected — the CV implementation lives
    elsewhere and is deferred pending in-game calibration. This function
    is the framework / sequencing entry point and is offline-testable
    by passing a fake.
    """
    if not enabled:
        return FssOutcome(result=FssResult.DISABLED)
    try:
        sender.press("ExplorationFSSEnter", hold=0.05)
    except KeyError:
        return FssOutcome(result=FssResult.NOT_BOUND)

    deadline = clock() + timeout_s
    scanned: list[str] = []
    blobs = blob_source.detect_blobs()
    for cx, cy in blobs:
        # Tune toward each blob by stepping the radio-tuning key. The
        # actual direction would be derived from blob HSV under calibration;
        # we emit one nominal step here for sequencing-test purposes.
        sender.press("ExplorationFSSRadioTuningX_Increase", hold=0.05)
        sender.press("ExplorationFSSTarget", hold=0.05)
        if clock() >= deadline:
            break

    for ev in events:
        if isinstance(ev, Scan):
            scanned.append(ev.body_name)
        if isinstance(ev, FSSAllBodiesFound):
            sender.press("ExplorationFSSQuit", hold=0.05)
            return FssOutcome(
                result=FssResult.ALL_BODIES,
                bodies_scanned=len(scanned),
                scanned_body_names=scanned,
            )
        if clock() >= deadline:
            break

    sender.press("ExplorationFSSQuit", hold=0.05)
    return FssOutcome(
        result=FssResult.TIMEOUT,
        bodies_scanned=len(scanned),
        scanned_body_names=scanned,
    )
