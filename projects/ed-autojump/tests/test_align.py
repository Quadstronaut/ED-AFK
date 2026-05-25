"""
Closed-loop alignment. We drive align_to_target with a tiny ship simulator:
each pitch/yaw press actually moves the dot toward centre, so a working
controller converges. The simulator also models a BEHIND target coming
'over the top' to the front as we pitch up.

The safety-critical assertion: when the compass can't be read (or time runs
out), align_to_target reports aligned=False. The engage gate (separate
module) relies on this to refuse a misaligned jump.
"""

from ed_autojump.executor.align import align_to_target, AlignOutcome
from ed_autojump.vision.compass import CompassRead


class _Sim:
    """A toy plant. offset_x>0 = dot right; offset_y>0 = dot above.
    Yaw-right reduces offset_x; pitch-up reduces offset_y. A behind target
    flips to in_front once pitched 'over the top' (offset_y goes <= 0)."""

    def __init__(self, ox=0.0, oy=0.0, in_front=True, found=True, k=1.0):
        self.ox, self.oy, self.in_front, self.found, self.k = ox, oy, in_front, found, k

    # acts as the sender
    def press(self, action, *, hold=0.05):
        d = self.k * hold
        if action == "PitchUpButton":
            self.oy -= d
            if not self.in_front and self.oy <= 0.0:
                self.in_front = True       # came over the top to the front
                self.oy = 0.15
        elif action == "PitchDownButton":
            self.oy += d
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
    assert abs(sim.ox) < 0.1


def test_converges_from_above():
    sim = _Sim(ox=0.0, oy=0.8, in_front=True)
    out = _run(sim)
    assert out.aligned is True
    assert abs(sim.oy) < 0.1


def test_behind_target_is_brought_around_then_aligned():
    sim = _Sim(ox=0.0, oy=0.9, in_front=False)
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
