"""
Closed-loop nav-compass alignment.

The old blind macro (perform_star_escape, DELETED 2026-06-06) pitched a
fixed time then engaged — it couldn't actually point at the next target,
which is why the ship "engaged the FSD but didn't orient". This loop
closes that gap: read the compass,
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
from typing import Any, Callable, Optional

from ..vision.compass import CompassRead, CompassReader


@dataclass
class AlignOutcome:
    aligned: bool
    iterations: int
    final: CompassRead
    reason: str  # "aligned" | "timeout" | "max_iters"


# front_fill uncertainty band (2026-06-06 boundary disease): the live fill
# histogram (4,473 real reads) has 821 reads in 0.3-0.6 — a median there is
# genuinely ambiguous, and re-thresholding it every beat flipped the verdict
# at STABLE positions (19% of live iterations had per-sample disagreement;
# 14 median-level flips). Inside the band the verdict HOLDS the caller's
# previous one; outside, the median always re-decides (no permanent stick).
_FILL_BAND_LO = 0.35
_FILL_BAND_HI = 0.65


def _measure(
    reader: Any,
    capture: Callable[[], Any],
    samples: int,
    *,
    raw_out: Optional[list] = None,
    frames_out: Optional[list] = None,
    prev_in_front: Optional[bool] = None,
) -> CompassRead:
    """Take ``samples`` consecutive reads and return a median-filtered result.

    With ``samples == 1`` the single read is returned unchanged (identical to
    the pre-median behaviour so existing tests don't need to change).

    With ``samples > 1``:
      - Collect all reads back-to-back (no sleep — the loop's settle handles pacing).
      - If fewer than half are ``found``, return ``CompassRead.not_found()``.
      - Otherwise return a synthetic read whose offset_x / offset_y are the
        statistical medians of the found reads and confidence is the mean —
        this rejects single-frame cyan-UI spikes.
      - in_front comes from the MEDIAN of the continuous ``front_fill``
        evidence (reads without it contribute 1.0/0.0 from their boolean),
        NOT from a vote of per-sample bits: bits at the classifier boundary
        are coin flips, the median fill is stable. When the median lands in
        the uncertainty band and ``prev_in_front`` is given, the previous
        verdict is held (temporal hysteresis at a stable position).

    ``raw_out`` / ``frames_out``: optional lists that collect the per-sample
    CompassReads / captured frames — diagnostic taps for align telemetry
    (the 2026-06-06 oscillation was undiagnosable from the recording because
    reads were never logged). No-cost when omitted.
    """
    if samples == 1:
        frame = capture()
        if frames_out is not None:
            frames_out.append(frame)
        read = reader.read(frame)
        if raw_out is not None:
            raw_out.append(read)
        return read

    reads = []
    for _ in range(samples):
        frame = capture()
        if frames_out is not None:
            frames_out.append(frame)
        reads.append(reader.read(frame))
    if raw_out is not None:
        raw_out.extend(reads)
    found_reads = [r for r in reads if r.found]

    # Require a STRICT majority to be found; ties count as not_found.
    # `<= samples // 2` rejects ties for even sample counts (e.g. 3-of-6 fails).
    if len(found_reads) <= samples // 2:
        return CompassRead.not_found()

    median_fill = statistics.median(
        (r.front_fill if r.front_fill is not None else (1.0 if r.in_front else 0.0))
        for r in found_reads
    )
    if prev_in_front is not None and _FILL_BAND_LO <= median_fill <= _FILL_BAND_HI:
        in_front = prev_in_front
    else:
        in_front = median_fill >= 0.5

    return CompassRead(
        found=True,
        offset_x=statistics.median(r.offset_x for r in found_reads),
        offset_y=statistics.median(r.offset_y for r in found_reads),
        in_front=in_front,
        confidence=sum(r.confidence for r in found_reads) / len(found_reads),
        front_fill=median_fill,
    )


def _press_for(offset: float, gain: float, min_press: float, max_press: float) -> float:
    """Proportional press duration for a given offset magnitude."""
    return max(min_press, min(max_press, gain * abs(offset)))


def _correct(sender: Any, read: CompassRead, *, gain: float, min_press: float,
             max_press: float, deadzone: float) -> Optional[tuple]:
    """One correction step toward the dot. Returns ``(action, hold)`` for the
    press it sent, or ``None`` when the dominant axis is inside the deadzone.

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
        action = "PitchDownButton" if read.offset_y < 0 else "PitchUpButton"
        sender.press(action, hold=max_press)
        return (action, max_press)
    # In front: yaw and pitch are coupled on the tilted disc, so correct only
    # the DOMINANT axis each step (the larger error) to avoid the two fighting.
    if abs(read.offset_x) >= abs(read.offset_y):
        if abs(read.offset_x) > deadzone:
            action = "YawRightButton" if read.offset_x > 0 else "YawLeftButton"
            hold = _press_for(read.offset_x, gain, min_press, max_press)
            sender.press(action, hold=hold)
            return (action, hold)
    else:
        if abs(read.offset_y) > deadzone:
            action = "PitchUpButton" if read.offset_y > 0 else "PitchDownButton"
            hold = _press_for(read.offset_y, gain, min_press, max_press)
            sender.press(action, hold=hold)
            return (action, hold)
    return None


def align_to_target(
    reader: CompassReader,
    sender: Any,
    *,
    capture: Callable[[], Any],
    align_tol: float = 0.12,
    # invariant: deadzone*sqrt(2) (=0.1131) <= align_tol so the per-axis
    # L-inf deadzone can never leave a corner pose un-aligned yet un-pressed
    deadzone: float = 0.08,
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
    on_iter: Optional[Callable[[dict], None]] = None,
    frame_sink: Optional[Callable[[int, list], None]] = None,
    abort_check: Optional[Callable[[], Optional[str]]] = None,
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

    Telemetry (ADDED 2026-06-06 — the 12:37 oscillation was undiagnosable
    from the session recording because reads were never logged):
    - ``on_iter(payload)``: one dict per iteration — median read, raw
      per-sample reads, and the press chosen (action None = no press:
      aligned, or dominant axis inside the deadzone).
    - ``frame_sink(i, frames)``: the iteration's captured frames, so a
      failing orient can be replayed offline against the reader. Frames are
      only retained when a sink is wired.

    ``abort_check`` (ADDED 2026-06-06 — the 13:26 star smack): polled every
    iteration; a truthy string aborts the loop immediately with that string
    as the outcome reason. The caller wires the flight-state precondition
    (e.g. "supercruise_lost") — after the ship emergency-dropped at a star,
    this loop kept steering normal-space glare garbage for 35s to timeout.
    """
    start = clock()
    last = CompassRead.not_found()
    behinds = 0  # consecutive behind reads — gates the hard behind-flip

    for i in range(max_iters):
        if clock() - start > timeout_s:
            return AlignOutcome(aligned=False, iterations=i, final=last, reason="timeout")
        if abort_check is not None:
            why = abort_check()
            if why:
                return AlignOutcome(aligned=False, iterations=i, final=last, reason=why)

        raw: Optional[list] = [] if on_iter is not None else None
        frames: Optional[list] = [] if frame_sink is not None else None
        # Thread the last FOUND verdict into the measurement so a median
        # fill in the uncertainty band holds it (hysteresis) instead of
        # re-flipping a coin at a stable position.
        prev = last.in_front if last.found else None
        read = _measure(reader, capture, samples, raw_out=raw,
                        frames_out=frames, prev_in_front=prev)
        last = read
        if frame_sink is not None:
            frame_sink(i, frames)

        aligned_now = read.found and read.in_front and read.magnitude <= align_tol

        # Behind-flicker damping (2026-06-06 watch-list item, mirrors
        # pitch_compass's front-flicker gate): a single flipped behind read
        # is classifier noise at the filled/hollow boundary, and the
        # behind-flip it fires is a max_press hard pitch that wrecks a
        # converging pose. The flip needs 2 CONSECUTIVE behind beats; one
        # spurious beat presses nothing and holds position. A real behind
        # target costs one extra settle cycle, a noise beat costs zero.
        behinds = behinds + 1 if (read.found and not read.in_front) else 0
        # Fill-aware damp (2026-06-07 session: a decisive astern read —
        # ox=-0.0307 oy=0.9908 in_front=False fill=0.161, far below
        # _FILL_BAND_LO — was damped, the 1.4s no-press settle let the SC
        # orbit swing the dot off-compass, 21 blind search iters, retry).
        # The damp is for BOUNDARY noise (fill ~0.5), so only damp when the
        # fill is in/above the band; a decisive low-fill astern read flips on
        # beat 0. None-fill reads (test fakes / non-fill backends) keep the
        # legacy 2-beat damp — the live loop always emits floats, so that
        # branch never fires in production. Inclusive >= keeps boundary fill
        # exactly 0.35 damped, consistent with _measure's own band test.
        behind_flicker = (read.found and not read.in_front and behinds < 2
                          and (read.front_fill is None
                               or read.front_fill >= _FILL_BAND_LO))

        action: Optional[str] = None
        hold: Optional[float] = None
        if aligned_now:
            pass                                   # no press — we're done
        elif not read.found:
            # Can't see the dot — rotate a little to bring it into view.
            action, hold = "YawRightButton", search_press
            sender.press(action, hold=hold)
        elif behind_flicker:
            pass                                   # damped — hold position
        else:
            pressed = _correct(sender, read, gain=gain, min_press=min_press,
                               max_press=max_press, deadzone=deadzone)
            if pressed is not None:
                action, hold = pressed

        if on_iter is not None:
            on_iter({
                "i": i,
                "found": read.found,
                "in_front": read.in_front,
                # median front_fill — the continuous evidence behind the
                # verdict (ADDED 2026-06-06: the boundary disease was only
                # quantifiable by re-running the reader over dumped frames).
                "fill": None if read.front_fill is None else round(read.front_fill, 3),
                "ox": round(read.offset_x, 4),
                "oy": round(read.offset_y, 4),
                "mag": round(read.magnitude, 4),
                "action": action,
                "hold": hold,
                "aligned": aligned_now,
                "raw": [[r.found, r.in_front,
                         round(r.offset_x, 4), round(r.offset_y, 4)]
                        for r in (raw or [])],
            })

        if aligned_now:
            return AlignOutcome(aligned=True, iterations=i, final=read, reason="aligned")
        sleeper(settle_s)

    return AlignOutcome(aligned=False, iterations=max_iters, final=last, reason="max_iters")
