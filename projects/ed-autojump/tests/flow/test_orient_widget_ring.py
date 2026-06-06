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


def _ctx(sender, reader, *, enabled=True, grabber=lambda: object(), clock=None,
         on_miss="fail_closed"):
    # Step tests default to fail_closed so miss paths are observable as
    # False; degrade-mode (the config default) has its own tests below.
    return StepContext(
        sender=sender,
        clock=clock or _Clock(),
        sleeper=lambda s: None,
        widget_ring_enabled=enabled,
        widget_ring_reader=reader,
        widget_frame_grabber=grabber,
        widget_ring_on_miss=on_miss,
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
# 13. flag on, no reader/grabber -> miss path (per widget_ring_on_miss)
# ---------------------------------------------------------------------------

def test_flag_on_no_reader_fails_closed():
    sender = FakeSender()
    ctx = StepContext(sender=sender, sleeper=lambda s: None,
                      widget_ring_enabled=True, widget_ring_reader=None,
                      widget_frame_grabber=lambda: object(),
                      widget_ring_on_miss="fail_closed")
    assert step_orient_widget_ring(ctx) is False
    assert sender.actions() == []

    sender2 = FakeSender()
    ctx2 = StepContext(sender=sender2, sleeper=lambda s: None,
                       widget_ring_enabled=True,
                       widget_ring_reader=_FakeRingReader([]),
                       widget_frame_grabber=None,
                       widget_ring_on_miss="fail_closed")
    assert step_orient_widget_ring(ctx2) is False
    assert sender2.actions() == []


def test_flag_on_no_reader_degrades_by_default():
    """Operator decision 2026-06-06 (issue #1): default on_miss='degrade' —
    a miss SKIPS the fine pass (True) so the compass-only jump proceeds."""
    sender = FakeSender()
    ctx = StepContext(sender=sender, sleeper=lambda s: None,
                      widget_ring_enabled=True, widget_ring_reader=None,
                      widget_frame_grabber=lambda: object())
    assert step_orient_widget_ring(ctx) is True   # degraded, not gated
    assert sender.actions() == []


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


def test_timeout_degrades_by_default():
    """Same no-convergence timeout under the default 'degrade' -> True; a
    genuinely bad aim is caught by the FSD charge aborting + autorecovery."""
    sender = FakeSender()
    reader = _FakeRingReader([_read(80, 0)])  # always off-axis
    ctx = _ctx(sender, reader, clock=_Clock(step=1.0), on_miss="degrade")
    assert step_orient_widget_ring(ctx, timeout_s=5.0, samples=1) is True


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
        widget_ring_on_miss="fail_closed",
        record=lambda t, p: logged.append((t, p)),
    )
    assert step_orient_widget_ring(ctx, timeout_s=4.0, samples=1) is False
    assert any(t == "BindMissing" for t, _ in logged)
    assert "YawRightButton" not in sender.actions()  # never recorded (raised)


# ---------------------------------------------------------------------------
# 19b-19d. per-iteration telemetry + frame dumps (ADDED 2026-06-06: the 13:0x
# WidgetRingTimeout iters=28 was undiagnosable from the recording — only the
# operator's screenshot exposed the phantom ring lock. Never again.)
# ---------------------------------------------------------------------------

def test_iter_telemetry_logged_every_iteration():
    logged = []
    sender = FakeSender()
    reader = _FakeRingReader([_read(80, 0), _read(0, 0)])
    ctx = StepContext(
        sender=sender, clock=_Clock(), sleeper=lambda s: None,
        widget_ring_enabled=True, widget_ring_reader=reader,
        widget_frame_grabber=lambda: object(),
        widget_ring_on_miss="fail_closed",
        record=lambda t, p: logged.append((t, p)),
    )
    assert step_orient_widget_ring(ctx, samples=1) is True
    iters = [p for t, p in logged if t == "WidgetRingIter"]
    assert len(iters) == 2                       # one row per loop iteration
    assert iters[0]["action"] == "YawRightButton"
    assert iters[0]["hold"] > 0
    assert iters[0]["aligned"] is False
    assert iters[1]["action"] is None            # aligned -> no press
    assert iters[1]["aligned"] is True
    assert len(iters[0]["raw"]) == 1             # one raw read per sample


def test_iter_telemetry_reports_not_found_beat():
    logged = []
    sender = FakeSender()
    reader = _FakeRingReader([_read(0, 0, found=False), _read(0, 0)])
    ctx = StepContext(
        sender=sender, clock=_Clock(), sleeper=lambda s: None,
        widget_ring_enabled=True, widget_ring_reader=reader,
        widget_frame_grabber=lambda: object(),
        widget_ring_on_miss="fail_closed",
        record=lambda t, p: logged.append((t, p)),
    )
    assert step_orient_widget_ring(ctx, samples=1) is True
    iters = [p for t, p in logged if t == "WidgetRingIter"]
    assert iters[0]["found"] is False
    assert iters[0]["action"] is None            # not found -> idle beat
    assert sender.actions() == []


def test_frame_sink_receives_all_sample_frames():
    dumped = []
    sender = FakeSender()
    reader = _FakeRingReader([_read(80, 0), _read(0, 0), _read(0, 0), _read(0, 0)])
    ctx = StepContext(
        sender=sender, clock=_Clock(), sleeper=lambda s: None,
        widget_ring_enabled=True, widget_ring_reader=reader,
        widget_frame_grabber=lambda: object(),
        widget_ring_on_miss="fail_closed",
        frame_sink=lambda name, frame: dumped.append(name),
    )
    step_orient_widget_ring(ctx, samples=2)
    assert all(n.startswith("widget_") for n in dumped)
    assert any("_i00_s0" in n for n in dumped)
    assert any("_i00_s1" in n for n in dumped)   # every sample frame dumped
    assert len(dumped) % 2 == 0                  # samples per iteration


# ---------------------------------------------------------------------------
# 19e. supercruise lost mid-step -> fail closed EVEN under degrade
# ---------------------------------------------------------------------------

class _FlipStatus:
    """in_supercruise True for the first `n_true` reads, then False."""

    def __init__(self, n_true):
        self.n = n_true
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return SimpleNamespace(in_supercruise=self.calls <= self.n)


def test_supercruise_lost_fails_closed_even_in_degrade_mode():
    """2026-06-06 13:26 star smack: losing supercruise mid-step is NOT a
    vision miss — degrade would walk the flow on to engage_jump in normal
    space inside the exclusion zone. Fail the required step so the procedure
    unwinds and the queued smack_recovery dispatch can run."""
    logged = []
    sender = FakeSender()
    reader = _FakeRingReader([_read(80, 0)])     # never aligns on its own
    ctx = StepContext(
        sender=sender, clock=_Clock(), sleeper=lambda s: None,
        widget_ring_enabled=True, widget_ring_reader=reader,
        widget_frame_grabber=lambda: object(),
        widget_ring_on_miss="degrade",           # the config default
        status_supplier=_FlipStatus(n_true=2),
        record=lambda t, p: logged.append((t, p)),
    )
    assert step_orient_widget_ring(ctx, timeout_s=999, samples=1) is False
    assert any(t == "WidgetRingAbort" and p["why"] == "supercruise_lost"
               for t, p in logged)
    assert len(sender.actions()) <= 3            # stopped pressing immediately


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


def _fsd_target_seq():
    """Supplier whose seq advances on every read — the read after the press
    always looks like a fresh, safe (K-class) FSDTarget."""
    n = [0]
    def supplier():
        n[0] += 1
        return (n[0], SimpleNamespace(event="FSDTarget", star_class="K"))
    return supplier


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
        # arrival now ends with hold_alignment (event/state-gated, no clock);
        # fire StartJump on the first poll so the procedure completes.
        event_waiter=lambda ev, t: ev == "StartJump",
        # target_next_route's danger gate needs a NEW safe FSDTarget after
        # the press (seq must advance past the pre-press snapshot).
        fsd_target_supplier=_fsd_target_seq(),
    )
    result = run_procedure(procs["arrival"], ctx)
    assert result.aborted is False
    assert "Hyperspace" in sender.actions()  # reached + fired the jump
