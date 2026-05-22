"""
Req 6 — DSS naive 6-direction probe pattern (SPEC §9.7).

DSS has no dedicated "enter" binding. The sequence is:

1. PlayerHUDModeToggle - Analysis Mode
2. CycleFireGroupNext N times to reach the DSS firegroup
3. Confirm Flags.InAnalysisMode (bit 27)
4. PrimaryFire to launch probes

This module sequences the keypresses. Body-aim CV is deferred pending
calibration; we expose interfaces so the in-game tier can plug in later.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable, Iterable

from ..journal.events import Event, SAAScanComplete
from ..keys.sender import Sender


DSS_PER_BODY_TIMEOUT_S = 120.0
DSS_NAIVE_DIRECTIONS = [
    # (yaw_action, pitch_action). 6-direction star pattern per SPEC §9.7.2.
    (None, None),                                    # forward
    (None, "SAAThirdPersonPitchUpButton"),           # up
    (None, "SAAThirdPersonPitchDownButton"),         # down
    ("SAAThirdPersonYawLeftButton", None),           # left
    ("SAAThirdPersonYawRightButton", None),          # right
    (None, "SAAThirdPersonPitchUpButton"),           # back-around (approx)
]


class DssResult(Enum):
    COMPLETE = auto()
    TIMEOUT = auto()
    DISABLED = auto()


@dataclass
class DssOutcome:
    result: DssResult
    body_name: str
    probes_used: int = 0
    efficiency_target: int = 0


def perform_dss_naive_pattern(
    sender: Sender,
    events: Iterable[Event],
    *,
    target_body: str,
    firegroup_cycles: int = 1,
    enabled: bool = True,
    timeout_s: float = DSS_PER_BODY_TIMEOUT_S,
    clock: Callable[[], float] = lambda: 0.0,
) -> DssOutcome:
    """
    Execute the entry sequence + 6 PrimaryFire shots in the star pattern.
    Watches for the matching `SAAScanComplete` event to confirm coverage.
    """
    if not enabled:
        return DssOutcome(result=DssResult.DISABLED, body_name=target_body)

    sender.press("PlayerHUDModeToggle", hold=0.05)
    for _ in range(max(0, firegroup_cycles)):
        sender.press("CycleFireGroupNext", hold=0.05)

    for yaw_action, pitch_action in DSS_NAIVE_DIRECTIONS:
        if yaw_action is not None:
            sender.press(yaw_action, hold=0.3)
        if pitch_action is not None:
            sender.press(pitch_action, hold=0.3)
        sender.press("PrimaryFire", hold=0.05)

    deadline = clock() + timeout_s
    for ev in events:
        if isinstance(ev, SAAScanComplete) and ev.body_name == target_body:
            return DssOutcome(
                result=DssResult.COMPLETE,
                body_name=ev.body_name,
                probes_used=ev.probes_used,
                efficiency_target=ev.efficiency_target,
            )
        if clock() >= deadline:
            break

    return DssOutcome(result=DssResult.TIMEOUT, body_name=target_body)
