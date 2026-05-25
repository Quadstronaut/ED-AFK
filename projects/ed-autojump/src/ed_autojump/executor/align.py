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

Temporal-median filtering: each measurement can take N consecutive frame
reads (``samples`` parameter). The median of found reads rejects transient
spikes — e.g. a competing cyan UI element that briefly outscores the dot.
On real hardware, set ``settle_s`` to ~0.6–0.8 s: the ship has rotational
momentum and keeps spinning ~1–1.5 s after a key release, so reading too
soon gives a mid-spin position and the proportional controller oscillates.
"""

from __future__ import annotations

import statistics
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


def _measure(reader: Any, capture: Callable[[], Any], samples: int) -> CompassRead:
    """Take ``samples`` consecutive reads and return a median-filtered result.

    With ``samples == 1`` the single read is returned unchanged (identical to
    the pre-median behaviour so existing tests don't need to change).

    With ``samples > 1``:
      - Collect all reads back-to-back (no sleep — the loop's settle handles pacing).
      - If fewer than half are ``found``, return ``CompassRead.not_found()``.
      - Otherwise return a synthetic read whose offset_x / offset_y are the
        statistical medians of the found reads, in_front is a majority vote,
        and confidence is the mean — this rejects single-frame cyan-UI spikes.
    """
    if samples == 1:
        return reader.read(capture())

    reads = [reader.read(capture()) for _ in range(samples)]
    found_reads = [r for r in reads if r.found]

    # Require a majority to be found; otherwise we can't trust any measurement.
    if len(found_reads) < samples / 2:
        return CompassRead.not_found()

    return CompassRead(
        found=True,
        offset_x=statistics.median(r.offset_x for r in found_reads),
        offset_y=statistics.median(r.offset_y for r in found_reads),
        in_front=sum(r.in_front for r in found_reads) > len(found_reads) / 2,
        confidence=sum(r.confidence for r in found_reads) / len(found_reads),
    )


def _press_for(offset: float, gain: float, min_press: float, max_press: float) -> float:
    """Proportional press duration for a given offset magnitude."""
    return max(min_press, min(max_press, gain * abs(offset)))


def _correct(sender: Any, read: CompassRead, *, gain: float, min_press: float,
             max_press: float, deadzone: float) -> None:
    """One correction step toward the dot.

    Behind-flip: when the target is behind (hollow dot), pitch HARD toward
    the dot's vertical side — down if the dot is low (offset_y < 0), up if
    high — to flip it over the nearest pole to the front. No yaw while
    behind; the coupled axis would fight the flip.

    Dominant-axis correction (in front): the compass disc is
    perspective-tilted, so a yaw press also moves the dot vertically and
    vice versa. Correcting both axes per step makes them fight and stall.
    Instead, correct only the DOMINANT axis (the one with the larger
    |offset|) each step — the other axis naturally follows.
    """
    if not read.in_front:
        # Behind: flip the target over the nearest pole to the front by
        # pitching hard toward the dot's vertical side. No yaw while behind.
        sender.press("PitchDownButton" if read.offset_y < 0 else "PitchUpButton",
                     hold=max_press)
        return
    # In front: yaw and pitch are coupled on the tilted disc, so correct only
    # the DOMINANT axis each step (the larger error) to avoid the two fighting.
    if abs(read.offset_x) >= abs(read.offset_y):
        if abs(read.offset_x) > deadzone:
            sender.press("YawRightButton" if read.offset_x > 0 else "YawLeftButton",
                         hold=_press_for(read.offset_x, gain, min_press, max_press))
    else:
        if abs(read.offset_y) > deadzone:
            sender.press("PitchUpButton" if read.offset_y > 0 else "PitchDownButton",
                         hold=_press_for(read.offset_y, gain, min_press, max_press))


def align_to_target(
    reader: CompassReader,
    sender: Any,
    *,
    capture: Callable[[], Any],
    align_tol: float = 0.15,
    deadzone: float = 0.10,
    gain: float = 2.0,
    min_press: float = 0.10,
    max_press: float = 0.70,
    search_press: float = 0.2,
    settle_s: float = 1.4,
    max_iters: int = 40,
    timeout_s: float = 45.0,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    samples: int = 7,
) -> AlignOutcome:
    """Drive pitch/yaw until the compass dot is centred and in front.

    Returns aligned=True only when the dot is FRONT and within `align_tol`
    of centre. Times out (aligned=False) if the compass can't be read or
    the budget is exhausted — the caller must NOT engage on a False.

    ``samples``: number of consecutive reads per measurement (default 7 —
    validated live). Temporal-median over 7 reads robustly rejects transient
    cyan UI spikes. ``settle_s=1.4`` gives the ship's rotational momentum
    time to decay after each key press (~1–1.5 s on real hardware) before
    taking a measurement; reads mid-rotation are unreliable.

    Validated control law (dominant-axis + behind-flip):
    - In front: correct only the DOMINANT axis (larger |offset|) per step.
      The disc is perspective-tilted so yaw/pitch are coupled; correcting
      both per step causes them to fight and stall.
    - Behind: pitch hard toward the dot's vertical side to flip the target
      over the nearest pole to the front. No yaw while behind.
    - Gain=2.0 + max_press=0.70 drives hard — safe because settle_s is long
      enough for each move to fully complete before the next read.
    - Converges front cases in 3–4 iterations, full behind→front ~15 iters,
      monotonic with no oscillation (validated 2026-05-24, real hardware).
    """
    start = clock()
    last = CompassRead.not_found()

    for i in range(max_iters):
        if clock() - start > timeout_s:
            return AlignOutcome(aligned=False, iterations=i, final=last, reason="timeout")

        read = _measure(reader, capture, samples)
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
