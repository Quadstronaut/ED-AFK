"""
step_orient_widget_ring — step-level tests (spec tests 12-19) + procedure
integration (test 22). No game, no real sleeps. A _FakeRingReader queues
WidgetRingReads; the shared FakeSender records presses.
"""

from pathlib import Path
from types import SimpleNamespace

from ed_autojump.flow import StepContext, load_procedures, run_procedure
from ed_autojump.flow.steps import step_orient_widget_ring
from ed_autojump.vision.widget_ring import WidgetRingRead
from ed_autojump.vision.compass import CompassRead
from tests.flow import FakeSender

PROC_DIR = Path(__file__).resolve().parents[2] / "procedures"


class _FakeRingReader:
    """read() ignores the frame and returns queued reads (last repeats)."""

    def __init__(self, reads):
        self._reads = list(reads)
        self.calls = 0

    def read(self, frame):
        self.calls += 1
        if not self._reads:
            return WidgetRingRead.not_found()
        if len(self._reads) == 1:
            return self._reads[0]
        return self._reads.pop(0)


class _Clock:
    """Monotonic fake: each call advances by `step` seconds."""

    def __init__(self, step=0.1):
        self.t = 0.0
        self.step = step

    def __call__(self):
        self.t += self.step
        return self.t


def _read(dx, dy, r=40.0, found=True):
    return WidgetRingRead(
        found=found, widget_cx=450.0, widget_cy=300.0,
        ring_cx=450.0 + dx, ring_cy=300.0 + dy, ring_radius_px=r,
        delta_x=dx, delta_y=dy, deadzone_px=0.55 * r,
    )


def _ctx(sender, reader, *, enabled=True, grabber=lambda: object(), clock=None):
    return StepContext(
        sender=sender,
        clock=clock or _Clock(),
        sleeper=lambda s: None,
        widget_ring_enabled=enabled,
        widget_ring_reader=reader,
        widget_frame_grabber=grabber,
    )


# ---------------------------------------------------------------------------
# 12. flag off -> no-op True, reader never touched
# ---------------------------------------------------------------------------

def test_noop_true_when_flag_off():
    sender = FakeSender()
    reader = _FakeRingReader([_read(80, 0)])
    ctx = _ctx(sender, reader, enabled=False)
    assert step_orient_widget_ring(ctx) is True
    assert sender.actions() == []
    assert reader.calls == 0


# ---------------------------------------------------------------------------
# 13. flag on, no reader/grabber -> fail closed
# ---------------------------------------------------------------------------

def test_flag_on_no_reader_fails_closed():
    sender = FakeSender()
    ctx = StepContext(sender=sender, sleeper=lambda s: None,
                      widget_ring_enabled=True, widget_ring_reader=None,
                      widget_frame_grabber=lambda: object())
    assert step_orient_widget_ring(ctx) is False
    assert sender.actions() == []

    sender2 = FakeSender()
    ctx2 = StepContext(sender=sender2, sleeper=lambda s: None,
                       widget_ring_enabled=True,
                       widget_ring_reader=_FakeRingReader([]),
                       widget_frame_grabber=None)
    assert step_orient_widget_ring(ctx2) is False
    assert sender2.actions() == []


# ---------------------------------------------------------------------------
# 14. not-aligned then aligned -> one press, then True
# ---------------------------------------------------------------------------

def test_aligns_then_returns_true():
    sender = FakeSender()
    # first read needs a yaw correction; second is aligned
    reader = _FakeRingReader([_read(80, 0), _read(0, 0)])
    ctx = _ctx(sender, reader)
    assert step_orient_widget_ring(ctx, samples=1) is True
    assert sender.actions() == ["YawRightButton"]


# ---------------------------------------------------------------------------
# 15-16. dominant axis
# ---------------------------------------------------------------------------

def test_dominant_axis_yaw():
    sender = FakeSender()
    # dx80,dy20,r40 (deadzone 22): |dx|>|dy| -> yaw right; then align to stop
    reader = _FakeRingReader([_read(80, 20), _read(0, 0)])
    ctx = _ctx(sender, reader)
    step_orient_widget_ring(ctx, samples=1)
    assert sender.actions() == ["YawRightButton"]


def test_dominant_axis_pitch_down():
    sender = FakeSender()
    # dx10,dy60,r40: |dy|>|dx| and dy>0 -> pitch DOWN (no inversion)
    reader = _FakeRingReader([_read(10, 60), _read(0, 0)])
    ctx = _ctx(sender, reader)
    step_orient_widget_ring(ctx, samples=1)
    assert sender.actions() == ["PitchDownButton"]


# ---------------------------------------------------------------------------
# 17. both axes within deadzone -> aligned, zero presses
# ---------------------------------------------------------------------------

def test_deadzone_arithmetic():
    sender = FakeSender()
    # dx18,dy15,r40 -> deadzone 22 -> both within -> aligned immediately
    reader = _FakeRingReader([_read(18, 15)])
    ctx = _ctx(sender, reader)
    assert step_orient_widget_ring(ctx, samples=1) is True
    assert sender.actions() == []


# ---------------------------------------------------------------------------
# 18. never aligns -> timeout, fail closed
# ---------------------------------------------------------------------------

def test_timeout_fails_closed():
    sender = FakeSender()
    reader = _FakeRingReader([_read(80, 0)])  # always off-axis
    ctx = _ctx(sender, reader, clock=_Clock(step=1.0))
    assert step_orient_widget_ring(ctx, timeout_s=5.0, samples=1) is False


# ---------------------------------------------------------------------------
# 19. bind missing -> caught, continues, times out without crashing
# ---------------------------------------------------------------------------

def test_bind_missing_is_caught():
    logged = []
    sender = FakeSender(unbound=["YawRightButton"])
    reader = _FakeRingReader([_read(80, 0)])  # always wants a yaw right
    ctx = StepContext(
        sender=sender, clock=_Clock(step=1.0), sleeper=lambda s: None,
        widget_ring_enabled=True, widget_ring_reader=reader,
        widget_frame_grabber=lambda: object(),
        record=lambda t, p: logged.append((t, p)),
    )
    assert step_orient_widget_ring(ctx, timeout_s=4.0, samples=1) is False
    assert any(t == "BindMissing" for t, _ in logged)
    assert "YawRightButton" not in sender.actions()  # never recorded (raised)


# ---------------------------------------------------------------------------
# 22. procedure integration: step inserted after orient_compass, no-ops when off
# ---------------------------------------------------------------------------

class _OkCompassReader:
    """Compass reader that always reports the dot centred (orient succeeds)."""

    def read(self, frame):
        return CompassRead(found=True, offset_x=0.0, offset_y=0.0,
                           in_front=True, confidence=1.0)


def _arrival_status():
    return SimpleNamespace(docked=False, in_supercruise=True, fsd_charging=False,
                           fsd_cooldown=False, fsd_mass_locked=False,
                           overheating=False)


def test_arrival_has_widget_ring_after_compass():
    procs = load_procedures(PROC_DIR)
    actions = [s.action for s in procs["arrival"].steps]
    # (a) structural: orient_widget_ring immediately follows orient_compass
    i = actions.index("orient_compass")
    assert actions[i + 1] == "orient_widget_ring"

    # (b) exercise the no-op path (not vacuously): compass SUCCEEDS, flag OFF ->
    # the inserted step is REACHED, no-ops True, and the flow proceeds to fire
    # the jump. Proves the insert didn't block the flow.
    sender = FakeSender()
    ctx = StepContext(
        sender=sender, sleeper=lambda s: None,
        compass_reader=_OkCompassReader(),
        frame_grabber=lambda: object(), compass_samples=1,
        status_supplier=_arrival_status,
        align_kwargs={"max_iters": 2, "timeout_s": 999, "settle_s": 0.0},
        widget_ring_enabled=False,  # no-op fine step
    )
    result = run_procedure(procs["arrival"], ctx)
    assert result.aborted is False
    assert "Hyperspace" in sender.actions()  # reached + fired the jump
