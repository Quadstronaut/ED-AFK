"""
Closed-loop alignment. We drive align_to_target with a tiny ship simulator:
each pitch/yaw press actually moves the dot toward centre, so a working
controller converges. The simulator also models a BEHIND target coming
'over the top' (or 'under the bottom') to the front as we pitch.

The safety-critical assertion: when the compass can't be read (or time runs
out), align_to_target reports aligned=False. The engage gate (separate
module) relies on this to refuse a misaligned jump.
"""

import math

import pytest

from ed_autojump.executor.align import align_to_target, AlignOutcome, _measure, _correct
from ed_vision.compass import CompassRead


class _Sim:
    """A toy plant. offset_x>0 = dot right; offset_y>0 = dot above.
    Yaw-right reduces offset_x; pitch-up reduces offset_y.
    A behind target flips to in_front when pitched over either pole:
    - PitchUpButton (oy < 0 before press): flips when oy crosses <= 0 -> in_front
    - PitchDownButton (oy > 0 before press): flips when oy crosses >= 0 -> in_front

    k=0.4 calibrates the plant gain to the default controller gain=2.0
    (effective loop gain k*gain=0.8 < 1) so all convergence tests pass
    without explicit gain overrides.
    """

    def __init__(self, ox=0.0, oy=0.0, in_front=True, found=True, k=0.4):
        self.ox, self.oy, self.in_front, self.found, self.k = ox, oy, in_front, found, k

    # acts as the sender
    def press(self, action, *, hold=0.05):
        d = self.k * hold
        if action == "PitchUpButton":
            self.oy -= d
            if not self.in_front and self.oy <= 0.0:
                self.in_front = True       # came over the top pole to the front
                self.oy = 0.15
        elif action == "PitchDownButton":
            self.oy += d
            if not self.in_front and self.oy >= 0.0:
                self.in_front = True       # came over the bottom pole to the front
                self.oy = -0.15
        elif action == "YawRightButton":
            self.ox -= d
        elif action == "YawLeftButton":
            self.ox += d
        return None

    # acts as the reader
    def read(self, frame):
        if not self.found:
            return CompassRead.not_found()
        return CompassRead(found=True, offset_x=self.ox, offset_y=self.oy,
                           in_front=self.in_front, confidence=0.9)


def _run(sim, **kw):
    defaults = dict(capture=lambda: None, sleeper=lambda s: None, clock=lambda: 0.0)
    defaults.update(kw)
    return align_to_target(sim, sim, **defaults)


def test_already_aligned_returns_immediately_without_pressing():
    sim = _Sim(ox=0.0, oy=0.0, in_front=True)
    events = []
    orig = sim.press
    sim.press = lambda a, *, hold=0.05: (events.append(a), orig(a, hold=hold))[1]
    out = _run(sim)
    assert out.aligned is True
    assert events == []          # nothing to correct


def test_converges_from_the_right():
    sim = _Sim(ox=0.8, oy=0.0, in_front=True)
    out = _run(sim)
    assert out.aligned is True
    assert abs(sim.ox) < 0.15


def test_converges_from_above():
    sim = _Sim(ox=0.0, oy=0.8, in_front=True)
    out = _run(sim)
    assert out.aligned is True
    assert abs(sim.oy) < 0.15


def test_behind_high_target_is_brought_around_then_aligned():
    """Behind with dot HIGH (offset_y > 0) -> PitchUpButton flips it over top."""
    sim = _Sim(ox=0.0, oy=0.9, in_front=False)
    out = _run(sim)
    assert out.aligned is True
    assert sim.in_front is True


def test_behind_low_target_is_brought_around_then_aligned():
    """Behind with dot LOW (offset_y < 0) -> PitchDownButton flips it under bottom."""
    sim = _Sim(ox=0.0, oy=-0.9, in_front=False)
    out = _run(sim)
    assert out.aligned is True
    assert sim.in_front is True


class _Recorder:
    """Sender that only records (action, hold) pairs."""

    def __init__(self):
        self.calls = []

    def press(self, action, *, hold=0.05):
        self.calls.append((action, hold))


def test_single_behind_flicker_does_not_fire_hard_flip():
    """2026-06-06 watch-list item: a SINGLE flipped behind median (classifier
    noise at the filled/hollow boundary) used to fire _correct's behind-flip
    -- a max_press hard pitch that wrecks a converging pose. The flip now
    needs 2 CONSECUTIVE behind reads, mirroring pitch_compass's
    front-flicker gate. One spurious behind beat -> press NOTHING."""
    reads = [
        CompassRead(found=True, offset_x=0.5, offset_y=0.0, in_front=True, confidence=0.9),
        # spurious behind read, same position -> must hold position
        CompassRead(found=True, offset_x=0.5, offset_y=0.1, in_front=False, confidence=0.9),
        # flicker passed -> normal proportional correction resumes
        CompassRead(found=True, offset_x=0.05, offset_y=0.0, in_front=True, confidence=0.9),
    ]
    sender = _Recorder()
    out = align_to_target(_SeqReader(reads), sender, capture=lambda: None,
                          sleeper=lambda s: None, clock=lambda: 0.0,
                          samples=1, max_iters=3)
    assert out.aligned is True
    actions = [a for a, _ in sender.calls]
    assert all(a in ("YawRightButton", "YawLeftButton") for a in actions), \
        f"behind-flip fired on a single flicked read: {actions}"
    assert len(actions) == 1     # iter0 yaw; iter1 damped (no press); iter2 aligned


def test_two_consecutive_behind_reads_fire_the_flip():
    """The damping must not eat REAL behind targets: the second consecutive
    behind read fires the hard flip as before."""
    reads = [
        CompassRead(found=True, offset_x=0.0, offset_y=0.6, in_front=False, confidence=0.9),
        CompassRead(found=True, offset_x=0.0, offset_y=0.6, in_front=False, confidence=0.9),
    ]
    sender = _Recorder()
    align_to_target(_SeqReader(reads), sender, capture=lambda: None,
                    sleeper=lambda s: None, clock=lambda: 0.0,
                    samples=1, max_iters=2)
    assert [a for a, _ in sender.calls] == ["PitchUpButton"], \
        "second consecutive behind read must fire the flip"


def test_behind_streak_resets_on_front_read():
    """behind, front, behind -- the two behind reads are NOT consecutive;
    neither may fire the flip."""
    reads = [
        CompassRead(found=True, offset_x=0.5, offset_y=0.1, in_front=False, confidence=0.9),
        CompassRead(found=True, offset_x=0.5, offset_y=0.0, in_front=True, confidence=0.9),
        CompassRead(found=True, offset_x=0.5, offset_y=0.1, in_front=False, confidence=0.9),
    ]
    sender = _Recorder()
    align_to_target(_SeqReader(reads), sender, capture=lambda: None,
                    sleeper=lambda s: None, clock=lambda: 0.0,
                    samples=1, max_iters=3)
    assert "PitchUpButton" not in [a for a, _ in sender.calls]
    assert "PitchDownButton" not in [a for a, _ in sender.calls]


def test_decisive_astern_fill_fires_flip_on_first_beat():
    """2026-06-07 regression guard: a DECISIVE astern read (front_fill far
    below _FILL_BAND_LO) must fire the behind-flip on beat 0, not be damped.
    Live failure: post-_measure ox=-0.0307 oy=0.9908 in_front=False fill=0.161
    (unambiguous astern) was damped by the old fill-blind 2-beat gate; the
    1.4s no-press settle let the SC orbit swing the dot off-compass -> 21
    blind search iters -> ProcedureRetry. The damp is for BOUNDARY noise
    (fill ~0.5) only; decisive low fills flip immediately. oy>0 -> PitchUp."""
    read = CompassRead(found=True, offset_x=-0.0307, offset_y=0.9908,
                       in_front=False, confidence=0.6, front_fill=0.161)
    sender = _Recorder()
    align_to_target(_SeqReader([read]), sender, capture=lambda: None,
                    sleeper=lambda s: None, clock=lambda: 0.0,
                    samples=1, max_iters=1)
    assert [a for a, _ in sender.calls] == ["PitchUpButton"], \
        "decisive astern read (fill 0.161) must fire the flip on beat 0"


def test_boundary_fill_behind_read_still_damped_one_beat():
    """The damp is preserved for genuinely ambiguous boundary reads: a single
    behind read with front_fill=0.45 (inside [0.35, 0.65]) presses NOTHING on
    that beat — the legacy 2-beat gate still guards the filled/hollow boundary."""
    read = CompassRead(found=True, offset_x=0.0, offset_y=0.6,
                       in_front=False, confidence=0.6, front_fill=0.45)
    sender = _Recorder()
    align_to_target(_SeqReader([read]), sender, capture=lambda: None,
                    sleeper=lambda s: None, clock=lambda: 0.0,
                    samples=1, max_iters=1)
    pitches = [a for a, _ in sender.calls
               if a in ("PitchUpButton", "PitchDownButton")]
    assert pitches == [], "boundary-fill behind read must stay damped one beat"


def test_never_found_reports_not_aligned():
    sim = _Sim(found=False)
    out = _run(sim, max_iters=5)
    assert out.aligned is False
    assert out.reason in ("timeout", "max_iters")


def test_timeout_reports_not_aligned():
    sim = _Sim(ox=0.5, in_front=True)
    # Sender that never moves the dot, plus a clock that blows the budget.
    sim.press = lambda a, *, hold=0.05: None
    ticks = iter([0.0, 6.0, 12.0, 18.0, 24.0])
    out = _run(sim, clock=lambda: next(ticks), timeout_s=10.0)
    assert out.aligned is False
    assert out.reason == "timeout"


def test_outcome_is_dataclass_with_final_read():
    sim = _Sim(ox=0.0, oy=0.0, in_front=True)
    out = _run(sim)
    assert isinstance(out, AlignOutcome)
    assert out.final.found is True


# ---------------------------------------------------------------------------
# _correct: unit tests for the validated dominant-axis + behind-flip law
# ---------------------------------------------------------------------------

class _RecordingSender:
    """Captures (action, hold) pairs without touching any ship state."""
    def __init__(self):
        self.calls = []

    def press(self, action, *, hold=0.05):
        self.calls.append((action, hold))


def _make_read(*, ox=0.0, oy=0.0, in_front=True):
    return CompassRead(found=True, offset_x=ox, offset_y=oy,
                       in_front=in_front, confidence=0.9)


_PARAMS = dict(gain=2.0, min_press=0.10, max_press=0.70, deadzone=0.10)


def test_correct_behind_low_presses_pitch_down():
    """Behind + dot LOW (oy < 0) -> PitchDownButton at max_press; no yaw."""
    s = _RecordingSender()
    _correct(s, _make_read(ox=0.3, oy=-0.5, in_front=False), **_PARAMS)
    actions = [c[0] for c in s.calls]
    assert actions == ["PitchDownButton"]
    assert s.calls[0][1] == 0.70   # hold == max_press


def test_correct_behind_high_presses_pitch_up():
    """Behind + dot HIGH (oy > 0) -> PitchUpButton at max_press; no yaw."""
    s = _RecordingSender()
    _correct(s, _make_read(ox=0.3, oy=0.5, in_front=False), **_PARAMS)
    actions = [c[0] for c in s.calls]
    assert actions == ["PitchUpButton"]
    assert s.calls[0][1] == 0.70   # hold == max_press


def test_correct_behind_no_yaw_when_x_dominant():
    """Behind: never yaw, even when |ox| > |oy|."""
    s = _RecordingSender()
    _correct(s, _make_read(ox=0.8, oy=-0.3, in_front=False), **_PARAMS)
    actions = [c[0] for c in s.calls]
    assert "YawRightButton" not in actions
    assert "YawLeftButton" not in actions
    assert len(actions) == 1   # only one pitch press


def test_correct_in_front_dominant_x_yaws_only():
    """In front with |ox| > |oy|: yaw press, NO pitch press that step."""
    s = _RecordingSender()
    _correct(s, _make_read(ox=0.5, oy=0.1, in_front=True), **_PARAMS)
    actions = [c[0] for c in s.calls]
    assert len(actions) == 1
    assert actions[0] in ("YawRightButton", "YawLeftButton")


def test_correct_in_front_dominant_y_pitches_only():
    """In front with |oy| > |ox|: pitch press, NO yaw press that step."""
    s = _RecordingSender()
    _correct(s, _make_read(ox=0.1, oy=0.5, in_front=True), **_PARAMS)
    actions = [c[0] for c in s.calls]
    assert len(actions) == 1
    assert actions[0] in ("PitchUpButton", "PitchDownButton")


def test_correct_in_front_yaw_direction():
    """In front, dot RIGHT (ox > 0, dominant) -> YawRightButton."""
    s = _RecordingSender()
    _correct(s, _make_read(ox=0.5, oy=0.0, in_front=True), **_PARAMS)
    assert s.calls[0][0] == "YawRightButton"


def test_correct_in_front_pitch_direction():
    """In front, dot HIGH (oy > 0, dominant) -> PitchUpButton."""
    s = _RecordingSender()
    _correct(s, _make_read(ox=0.0, oy=0.5, in_front=True), **_PARAMS)
    assert s.calls[0][0] == "PitchUpButton"


def test_correct_in_front_within_deadzone_no_press():
    """In front, dominant axis within deadzone -> no press at all."""
    s = _RecordingSender()
    # |ox|=0.08 < deadzone=0.10; |oy|=0.05 < deadzone; both sub-deadzone
    _correct(s, _make_read(ox=0.08, oy=0.05, in_front=True), **_PARAMS)
    assert s.calls == []


def test_correct_in_front_equal_offsets_yaws():
    """Tie (|ox| == |oy|): the >= branch means x wins -> yaw press."""
    s = _RecordingSender()
    _correct(s, _make_read(ox=0.5, oy=0.5, in_front=True), **_PARAMS)
    actions = [c[0] for c in s.calls]
    assert len(actions) == 1
    assert actions[0] in ("YawRightButton", "YawLeftButton")


# ---------------------------------------------------------------------------
# _measure: temporal-median helper
# ---------------------------------------------------------------------------

class _SeqReader:
    """Reader that returns reads from a pre-built sequence in order."""

    def __init__(self, reads):
        self._it = iter(reads)

    def read(self, frame):
        return next(self._it)


def test_measure_median_rejects_spike():
    """A single 0.9 spike among four 0.1 reads must not skew the median."""
    reads = [
        CompassRead(found=True, offset_x=0.1, offset_y=0.0, in_front=True, confidence=0.9),
        CompassRead(found=True, offset_x=0.1, offset_y=0.0, in_front=True, confidence=0.9),
        CompassRead(found=True, offset_x=0.9, offset_y=0.0, in_front=True, confidence=0.9),  # spike
        CompassRead(found=True, offset_x=0.1, offset_y=0.0, in_front=True, confidence=0.9),
        CompassRead(found=True, offset_x=0.1, offset_y=0.0, in_front=True, confidence=0.9),
    ]
    reader = _SeqReader(reads)
    result = _measure(reader, lambda: None, samples=5)
    assert result.found is True
    assert result.offset_x == 0.1   # median of [0.1, 0.1, 0.9, 0.1, 0.1] = 0.1


def test_measure_single_sample_passthrough():
    """samples=1 must return the exact object from reader.read() unchanged."""
    singleton = CompassRead(found=True, offset_x=0.42, offset_y=0.1, in_front=True, confidence=0.8)
    reader = _SeqReader([singleton])
    result = _measure(reader, lambda: None, samples=1)
    assert result is singleton


def test_measure_majority_not_found():
    """Fewer than half found -> not_found result."""
    reads = [
        CompassRead.not_found(),
        CompassRead.not_found(),
        CompassRead.not_found(),
        CompassRead(found=True, offset_x=0.1, offset_y=0.0, in_front=True, confidence=0.9),
        CompassRead(found=True, offset_x=0.1, offset_y=0.0, in_front=True, confidence=0.9),
    ]
    reader = _SeqReader(reads)
    result = _measure(reader, lambda: None, samples=5)
    assert result.found is False


def _fill_read(fill, *, in_front=None, ox=0.1):
    """A found read carrying continuous front_fill evidence (in_front derives
    from fill unless given — mirrors what CyanDotReader emits per frame)."""
    return CompassRead(found=True, offset_x=ox, offset_y=0.0,
                       in_front=(fill >= 0.5) if in_front is None else in_front,
                       confidence=0.6, front_fill=fill)


def test_measure_medians_continuous_fill_not_boolean_votes():
    """2026-06-06 boundary disease: 19% of live iterations had per-sample
    in_front DISAGREEMENT (fills straddling 0.5). The median of the
    CONTINUOUS fills decides — sample-level boolean votes don't exist
    anymore. fills [.42,.46,.48,.52,.55] -> median .48 -> behind, even
    though boolean voting would say 2/5 front."""
    reader = _SeqReader([_fill_read(f) for f in (0.42, 0.46, 0.48, 0.52, 0.55)])
    result = _measure(reader, lambda: None, samples=5)
    assert result.found is True
    assert result.in_front is False
    assert result.front_fill == 0.48


def test_measure_uncertainty_band_holds_previous_verdict():
    """Median fill inside [0.35, 0.65] is genuinely ambiguous (the live
    histogram has 821 reads in 0.3-0.6) — with prev_in_front given, the
    verdict is STICKY so a stable position can't flip beat to beat."""
    fills = (0.45, 0.48, 0.52, 0.55, 0.49)
    reader = _SeqReader([_fill_read(f) for f in fills])
    held = _measure(reader, lambda: None, samples=5, prev_in_front=True)
    assert held.in_front is True, "band read must hold the previous verdict"

    reader = _SeqReader([_fill_read(f) for f in fills])
    held = _measure(reader, lambda: None, samples=5, prev_in_front=False)
    assert held.in_front is False

    reader = _SeqReader([_fill_read(f) for f in fills])
    fresh = _measure(reader, lambda: None, samples=5, prev_in_front=None)
    assert fresh.in_front is False, "no prior verdict -> plain 0.5 threshold"


def test_measure_clear_evidence_overrides_previous_verdict():
    """Hysteresis must not make the verdict permanently sticky: a median
    fill OUTSIDE the band always re-decides."""
    reader = _SeqReader([_fill_read(f) for f in (0.9, 0.95, 1.0, 0.92, 0.97)])
    result = _measure(reader, lambda: None, samples=5, prev_in_front=False)
    assert result.in_front is True

    reader = _SeqReader([_fill_read(f) for f in (0.1, 0.15, 0.2, 0.12, 0.17)])
    result = _measure(reader, lambda: None, samples=5, prev_in_front=True)
    assert result.in_front is False


def test_measure_fill_none_falls_back_to_boolean():
    """Readers that don't emit front_fill (YOLO, OpenCV, test fakes) must
    keep the old majority behaviour: fill defaults to in_front as 1.0/0.0."""
    reads = [CompassRead(found=True, offset_x=0.1, offset_y=0.0,
                         in_front=f, confidence=0.9) for f in (True, True, True, False, False)]
    result = _measure(_SeqReader(reads), lambda: None, samples=5)
    assert result.in_front is True   # median of [1,1,1,0,0] = 1.0
    assert result.front_fill == 1.0


def test_align_converges_with_samples():
    """samples=3 path: the loop still drives the ship to aligned=True."""
    sim = _Sim(ox=0.5, oy=0.0, in_front=True)
    out = _run(sim, samples=3)
    assert out.aligned is True
    assert abs(sim.ox) < 0.15


def test_measure_strict_majority_even_samples_tie_is_not_found():
    """samples=6, exactly 3 found (50/50 tie) -> not_found (strict majority required)."""
    reads = [
        CompassRead(found=True, offset_x=0.1, offset_y=0.0, in_front=True, confidence=0.9),
        CompassRead(found=True, offset_x=0.1, offset_y=0.0, in_front=True, confidence=0.9),
        CompassRead(found=True, offset_x=0.1, offset_y=0.0, in_front=True, confidence=0.9),
        CompassRead.not_found(),
        CompassRead.not_found(),
        CompassRead.not_found(),
    ]
    reader = _SeqReader(reads)
    result = _measure(reader, lambda: None, samples=6)
    assert result.found is False, "50/50 tie must be treated as not_found"


def test_on_iter_reports_every_iteration_with_action_and_raw_reads():
    """on_iter fires once per loop iteration with the median read, the press
    chosen, and the raw per-sample reads — the recording gap that made the
    2026-06-06 12:37 oscillation undiagnosable from the session jsonl."""
    sim = _Sim(ox=0.8, oy=0.0, in_front=True)
    payloads = []
    out = _run(sim, samples=3, on_iter=payloads.append)
    assert out.aligned is True
    # one payload per iteration, including the final aligned one (action None)
    assert len(payloads) == out.iterations + 1
    first, last = payloads[0], payloads[-1]
    assert first["i"] == 0
    assert first["found"] is True and first["in_front"] is True
    assert first["action"] == "YawRightButton" and first["hold"] > 0
    assert len(first["raw"]) == 3            # [found, in_front, ox, oy] per sample
    assert first["raw"][0][0] is True
    assert last["action"] is None and last["aligned"] is True


def test_on_iter_reports_behind_flip_at_max_press():
    sim = _Sim(ox=0.0, oy=0.9, in_front=False)
    payloads = []
    out = _run(sim, on_iter=payloads.append)
    assert out.aligned is True
    # 2026-06-07 fill-aware damp: _Sim reads carry no front_fill, so _measure
    # (samples=7) synthesizes front_fill from the in_front bit -> 0.0 here, a
    # DECISIVE astern read far below _FILL_BAND_LO. The damp is only for
    # boundary noise (fill in/above the band), so the flip fires on beat 0;
    # there is no longer a free first behind beat. (This test previously
    # pinned the exact pre-fix behaviour: damp-on-first-behind-beat.)
    assert payloads[0]["in_front"] is False
    assert payloads[0]["action"] == "PitchUpButton"
    assert payloads[0]["hold"] == 0.70       # behind-flip is always max_press


def test_on_iter_reports_search_when_not_found():
    sim = _Sim(found=False)
    payloads = []
    _run(sim, max_iters=2, on_iter=payloads.append)
    assert len(payloads) == 2
    assert payloads[0]["found"] is False
    assert payloads[0]["action"] == "YawRightButton"
    assert payloads[0]["hold"] == 0.2        # search_press default


def test_frame_sink_receives_all_sample_frames_per_iteration():
    """frame_sink(i, frames) gets the captured frames so failing orients can
    be replayed offline against the reader. Only called when provided."""
    sim = _Sim(ox=0.8, oy=0.0, in_front=True)
    sunk = []
    out = _run(sim, samples=3, capture=lambda: object(),
               frame_sink=lambda i, frames: sunk.append((i, list(frames))))
    assert out.aligned is True
    assert len(sunk) == out.iterations + 1
    assert sunk[0][0] == 0
    assert len(sunk[0][1]) == 3              # one frame per sample


def test_measure_strict_majority_odd_samples_4_of_7_is_found():
    """samples=7, exactly 4 found (>50%) -> returns a real read (same as before fix)."""
    reads = [
        CompassRead(found=True, offset_x=0.2, offset_y=0.0, in_front=True, confidence=0.9),
        CompassRead(found=True, offset_x=0.2, offset_y=0.0, in_front=True, confidence=0.9),
        CompassRead(found=True, offset_x=0.2, offset_y=0.0, in_front=True, confidence=0.9),
        CompassRead(found=True, offset_x=0.2, offset_y=0.0, in_front=True, confidence=0.9),
        CompassRead.not_found(),
        CompassRead.not_found(),
        CompassRead.not_found(),
    ]
    reader = _SeqReader(reads)
    result = _measure(reader, lambda: None, samples=7)
    assert result.found is True, "4-of-7 is a strict majority and must pass"
    assert result.offset_x == 0.2


def test_abort_check_stops_loop_with_its_reason():
    """2026-06-06 13:26 star smack: the ship emergency-dropped out of
    supercruise 10s into orient and the loop kept steering glare garbage in
    normal space for 35 more seconds. abort_check is polled every iteration;
    a truthy string ends the loop immediately as that failure reason."""
    sim = _Sim(ox=0.8, oy=0.0, in_front=True)   # would converge if allowed
    calls = [0]

    def sc_lost():
        calls[0] += 1
        return "supercruise_lost" if calls[0] >= 2 else None

    out = _run(sim, abort_check=sc_lost)
    assert out.aligned is False
    assert out.reason == "supercruise_lost"
    assert out.iterations <= 2                   # died fast, not at timeout


# ---------------------------------------------------------------------------
# F-A: forbidden-zone orient freeze (council-ratified 2026-06-07)
#
# DEFECT: aligned test is L2 (mag <= align_tol) but _correct's deadzone is
# per-axis L-inf. With the OLD live pair align_tol=0.10, deadzone=0.10 the
# corner region {both axes < deadzone, mag > align_tol} is a forbidden zone:
# un-aligned yet never pressed -> the loop freezes to timeout. Observed live:
# 22 frozen iters at ox=-0.0885, oy=0.080, mag=0.1193 -> timeout ->
# ProcedureRetry. Invariant that empties the zone: deadzone*sqrt(2) <= align_tol.
# Ratified: align_tol=0.12, deadzone=0.08 (0.08*sqrt(2)=0.1131 <= 0.12).
# ---------------------------------------------------------------------------

class _ConstReader:
    """Reader that always returns the same read (a frozen live frame)."""

    def __init__(self, read):
        self._read = read

    def read(self, frame):
        return self._read


# the exact live frame that froze for 22 iterations
_FROZEN = CompassRead(found=True, offset_x=-0.0885, offset_y=0.080,
                      in_front=True, confidence=0.9)


def test_frozen_live_frame_is_aligned_under_new_defaults():
    """T1: the real frozen frame (mag 0.1193) must read aligned at iter 0
    under the ratified 0.12/0.08 defaults — zero presses, reason 'aligned'."""
    sender = _Recorder()
    out = align_to_target(_ConstReader(_FROZEN), sender, capture=lambda: None,
                          sleeper=lambda s: None, clock=lambda: 0.0,
                          samples=1, max_iters=40)
    assert out.aligned is True
    assert out.reason == "aligned"
    assert out.iterations == 0
    assert sender.calls == []


@pytest.mark.parametrize("align_tol,deadzone", [(0.10, 0.10)])
def test_frozen_live_frame_freezes_under_old_constants(align_tol, deadzone):
    """T1 (documented bug): under the OLD constants the same frame is the
    forbidden zone — un-aligned (mag 0.1193 > 0.10) yet every axis is inside
    the 0.10 deadzone so _correct presses NOTHING. The loop never converges."""
    sender = _Recorder()
    out = align_to_target(_ConstReader(_FROZEN), sender, capture=lambda: None,
                          sleeper=lambda s: None, clock=lambda: 0.0,
                          samples=1, max_iters=5,
                          align_tol=align_tol, deadzone=deadzone)
    assert out.aligned is False           # the freeze: forbidden zone
    assert sender.calls == []             # never presses -> can't escape


def test_diagonal_hole_fires_a_press_and_converges():
    """T2: ox=oy=0.095 (mag 0.134) was frozen under the REJECTED 0.12/0.10
    pair (both axes 0.095 < 0.10 deadzone, mag > 0.12). Under 0.12/0.08 the
    axes clear the deadzone (0.095 > 0.08) so a yaw press fires, and the sim
    converges with no timeout."""
    # static-press check: at least one yaw press on the frozen diagonal frame
    sender = _Recorder()
    diag = CompassRead(found=True, offset_x=0.095, offset_y=0.095,
                       in_front=True, confidence=0.9)
    align_to_target(_ConstReader(diag), sender, capture=lambda: None,
                    sleeper=lambda s: None, clock=lambda: 0.0,
                    samples=1, max_iters=1, align_tol=0.12, deadzone=0.08)
    yaws = [a for a, _ in sender.calls if a in ("YawRightButton", "YawLeftButton")]
    assert len(yaws) >= 1, "deadzone 0.08 must let the 0.095 axis press"

    # closed-loop convergence with the real plant
    sim = _Sim(ox=0.095, oy=0.095, in_front=True)
    out = _run(sim, align_tol=0.12, deadzone=0.08)
    assert out.aligned is True
    assert out.reason == "aligned"


def test_invariant_holds_and_forbidden_zone_is_empty():
    """T3: the ratified invariant deadzone*sqrt(2) <= align_tol, plus a 25-pose
    sweep proving no pose can sit inside both axes' deadzone yet outside
    align_tol — the forbidden zone is empty by construction."""
    assert 0.08 * math.sqrt(2) <= 0.12
    grid = (-0.08, -0.04, 0.0, 0.04, 0.08)
    poses = [(ox, oy) for ox in grid for oy in grid]
    assert len(poses) == 25
    for ox, oy in poses:
        mag = math.hypot(ox, oy)
        # every in-deadzone pose is also inside align_tol -> no freeze
        assert mag <= 0.12, f"pose ({ox},{oy}) mag {mag} exceeds align_tol"


def test_convergence_no_oscillation_from_single_axis():
    """T4: starting ox=0.09, oy=0.0, the plant (k=0.4, gain=2.0) predicts
    press 0.18s -> lands +0.018 in one beat. Assert aligned within <=3 iters
    and no same-axis direction reversal in the press sequence."""
    sim = _Sim(ox=0.09, oy=0.0, in_front=True)
    sender_log = []
    orig = sim.press

    def logged(action, *, hold=0.05):
        sender_log.append(action)
        return orig(action, hold=hold)

    sim.press = logged
    out = _run(sim, align_tol=0.12, deadzone=0.08)
    assert out.aligned is True
    assert out.iterations <= 3
    # no same-axis reversal: never both YawRight and YawLeft in the sequence
    assert not ("YawRightButton" in sender_log and "YawLeftButton" in sender_log), \
        f"yaw direction reversed (oscillation): {sender_log}"
    assert not ("PitchUpButton" in sender_log and "PitchDownButton" in sender_log), \
        f"pitch direction reversed (oscillation): {sender_log}"
