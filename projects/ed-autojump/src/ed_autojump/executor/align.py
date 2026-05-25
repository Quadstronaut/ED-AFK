"""
Closed-loop nav-compass alignment.

The blind macro (`perform_star_escape`) pitches a fixed time then engages —
it can't actually point at the next target, which is why the ship "engaged
the FSD but didn't orient". This loop closes that gap: read the compass,
press pitch/yaw proportional to the dot's offset, repeat until the dot is
centred and in FRONT, or give up.

It only *reports* whether it aligned. It does NOT press the FSD — the
engage gate (orchestrator) decides to jump based on `aligned`, so a failed
alignment can never trigger a misaligned jump.

Everything external (reader, sender, frame capture, clock, sleep) is
injected, so the loop is unit-tested against a simulated ship with no game.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from ..vision.compass import CompassRead, CompassReader


@dataclass
class AlignOutcome:
    aligned: bool
    iterations: int
    final: CompassRead
    reason: str  # "aligned" | "timeout" | "max_iters"


def _press_for(offset: float, gain: float, min_press: float, max_press: float) -> float:
    """Proportional press duration for a given offset magnitude."""
    return max(min_press, min(max_press, gain * abs(offset)))


def _correct(sender: Any, read: CompassRead, *, gain: float, min_press: float,
             max_press: float, deadzone: float) -> None:
    """One correction step toward the dot.

    If the target is behind, drive pitch up hard to bring it over the top
    to the front (and nudge yaw toward it); once in front, correct both
    axes proportionally.
    """
    if not read.in_front:
        sender.press("PitchUpButton", hold=max_press)
        if abs(read.offset_x) > deadzone:
            sender.press("YawRightButton" if read.offset_x > 0 else "YawLeftButton",
                         hold=_press_for(read.offset_x, gain, min_press, max_press))
        return

    if abs(read.offset_y) > deadzone:
        action = "PitchUpButton" if read.offset_y > 0 else "PitchDownButton"
        sender.press(action, hold=_press_for(read.offset_y, gain, min_press, max_press))
    if abs(read.offset_x) > deadzone:
        action = "YawRightButton" if read.offset_x > 0 else "YawLeftButton"
        sender.press(action, hold=_press_for(read.offset_x, gain, min_press, max_press))


def align_to_target(
    reader: CompassReader,
    sender: Any,
    *,
    capture: Callable[[], Any],
    align_tol: float = 0.08,
    deadzone: float = 0.05,
    gain: float = 0.4,
    min_press: float = 0.03,
    max_press: float = 0.4,
    search_press: float = 0.2,
    settle_s: float = 0.12,
    max_iters: int = 60,
    timeout_s: float = 20.0,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> AlignOutcome:
    """Drive pitch/yaw until the compass dot is centred and in front.

    Returns aligned=True only when the dot is FRONT and within `align_tol`
    of centre. Times out (aligned=False) if the compass can't be read or
    the budget is exhausted — the caller must NOT engage on a False.
    """
    start = clock()
    last = CompassRead.not_found()

    for i in range(max_iters):
        if clock() - start > timeout_s:
            return AlignOutcome(aligned=False, iterations=i, final=last, reason="timeout")

        read = reader.read(capture())
        last = read

        if not read.found:
            # Can't see the dot — rotate a little to bring it into view.
            sender.press("YawRightButton", hold=search_press)
            sleeper(settle_s)
            continue

        if read.in_front and read.magnitude <= align_tol:
            return AlignOutcome(aligned=True, iterations=i, final=read, reason="aligned")

        _correct(sender, read, gain=gain, min_press=min_press,
                 max_press=max_press, deadzone=deadzone)
        sleeper(settle_s)

    return AlignOutcome(aligned=False, iterations=max_iters, final=last, reason="max_iters")
