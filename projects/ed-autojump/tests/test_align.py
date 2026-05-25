"""
Closed-loop alignment. We drive align_to_target with a tiny ship simulator:
each pitch/yaw press actually moves the dot toward centre, so a working
controller converges. The simulator also models a BEHIND target coming
'over the top' (or 'under the bottom') to the front as we pitch.

The safety-critical assertion: when the compass can't be read (or time runs
out), align_to_target reports aligned=False. The engage gate (separate
module) relies on this to refuse a misaligned jump.
"""

from ed_autojump.executor.align import align_to_target, AlignOutcome, _measure, _correct
from ed_autojump.vision.compass import CompassRead


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
