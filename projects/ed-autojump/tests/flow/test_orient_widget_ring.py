"""
step_orient_widget_ring — step-level tests (spec tests 12-19) + procedure
integration (test 22). No game, no real sleeps. A _FakeRingReader queues
WidgetRingReads; the shared FakeSender records presses.
"""

from pathlib import Path
from types import SimpleNamespace

from ed_autojump.flow import StepContext, load_procedures, run_procedure
from ed_autojump.flow.steps import step_orient_widget_ring
from ed_vision.widget_ring import WidgetRingRead
from ed_vision.compass import CompassRead
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
# 22. procedure integration
# ---------------------------------------------------------------------------
# NOTE (flow-redesign #1, 2026-06-27): test_arrival_has_widget_ring_after_compass
# was DELETED. arrival.toml no longer orients or jumps (its action list is
# [set_throttle, scoop_refuel, nav_supercruise_star]), so it has no
# orient_compass / orient_widget_ring at all. The "orient_widget_ring immediately
# follows orient_compass" order is now asserted for TRAVERSAL in
# test_traversal_flow.test_step_order_is_operator_verbatim, and the widget's
# no-op-when-off contract is covered by test_noop_true_when_flag_off above.


# ---------------------------------------------------------------------------
# 23. phantom-lock movement guard (operator 2026-07-13, LIVE dense-Colonia):
# a FIXED far cockpit HUD ring that never moves under correction presses must
# abort early (degrade) instead of pitching at it for the whole timeout.
# ---------------------------------------------------------------------------

def test_phantom_lock_aborts_early():
    """A FIXED far ring (the cockpit nav/radar disc: dx-213/dy318, unmoving) ->
    WidgetRingPhantomStuck after ~phantom_stuck_iters, NOT the full timeout.
    All 6 live timeouts pitched at exactly this fixed point for 18 iters."""
    logged = []
    sender = FakeSender()
    reader = _FakeRingReader([_read(-213, 318)])          # fixed far ring, repeats
    ctx = StepContext(
        sender=sender, clock=_Clock(step=1.0), sleeper=lambda s: None,
        widget_ring_enabled=True, widget_ring_reader=reader,
        widget_frame_grabber=lambda: object(),
        widget_ring_on_miss="degrade",
        record=lambda t, p: logged.append((t, p)),
    )
    assert step_orient_widget_ring(ctx, timeout_s=999.0, samples=1,
                                   phantom_stuck_iters=4) is True     # degraded
    assert any(t == "WidgetRingPhantomStuck" for t, _ in logged)
    assert not any(t == "WidgetRingTimeout" for t, _ in logged)
    iters = [p for t, p in logged if t == "WidgetRingIter"]
    assert len(iters) <= 6                                # bailed early, not 999s


def test_phantom_guard_lets_a_moving_ring_converge():
    """A real reticle that MOVES toward the widget under correction must NOT trip
    the phantom guard -- it converges and aligns normally."""
    logged = []
    sender = FakeSender()
    reader = _FakeRingReader([_read(-200, 300), _read(-120, 180),
                              _read(-40, 60), _read(0, 0)])
    ctx = StepContext(
        sender=sender, clock=_Clock(), sleeper=lambda s: None,
        widget_ring_enabled=True, widget_ring_reader=reader,
        widget_frame_grabber=lambda: object(),
        widget_ring_on_miss="degrade",
        record=lambda t, p: logged.append((t, p)),
    )
    assert step_orient_widget_ring(ctx, samples=1) is True
    assert not any(t == "WidgetRingPhantomStuck" for t, _ in logged)


def test_phantom_guard_ignores_near_ring_fine_tuning():
    """Near-centre fine-tuning (dist < phantom_min_dist) is never a phantom even
    if it hovers -- the distance gate protects it (only a FAR fixed ring is a
    cockpit lock)."""
    logged = []
    sender = FakeSender()
    reader = _FakeRingReader([_read(30, 0)])              # dist 30 < 50, unmoving
    ctx = StepContext(
        sender=sender, clock=_Clock(step=1.0), sleeper=lambda s: None,
        widget_ring_enabled=True, widget_ring_reader=reader,
        widget_frame_grabber=lambda: object(),
        widget_ring_on_miss="degrade",
        record=lambda t, p: logged.append((t, p)),
    )
    step_orient_widget_ring(ctx, timeout_s=5.0, samples=1)
    assert not any(t == "WidgetRingPhantomStuck" for t, _ in logged)


# ---------------------------------------------------------------------------
# ACQUISITION bridge (council 2026-07-14): a not-found widget beat now takes a
# bounded, compass-directed nudge instead of dead-waiting to timeout_s. Reuses
# align._measure/_correct on the COMPASS sign convention.
# ---------------------------------------------------------------------------

class _FakeCompassReader:
    """read() ignores the frame, returns queued CompassReads (last repeats).
    Counts calls so AC7 can prove the found-path never consults the compass."""

    def __init__(self, reads):
        self._reads = list(reads)
        self.calls = 0

    def read(self, frame):
        self.calls += 1
        if not self._reads:
            return CompassRead.not_found()
        if len(self._reads) == 1:
            return self._reads[0]
        return self._reads.pop(0)


def _cread(ox, oy, in_front=True, found=True):
    if not found:
        return CompassRead.not_found()
    return CompassRead(found=True, offset_x=ox, offset_y=oy,
                       in_front=in_front, confidence=1.0, front_fill=1.0)


def _acq_ctx(sender, ring, comp, *, on_miss, clock, logged, status=None):
    # comp=None -> compass unwired (frame_grabber also None), so acquisition
    # cannot press (AC6). Otherwise wire the compass pair orient_compass uses.
    # status_supplier is only passed when given: StepContext defaults it to
    # `lambda: None` (a callable) and _ensure_cockpit_focus calls it directly,
    # so an explicit None would crash — omit it to keep the SC guard unarmed.
    kw = dict(
        sender=sender, clock=clock, sleeper=lambda s: None,
        widget_ring_enabled=True, widget_ring_reader=ring,
        widget_frame_grabber=lambda: object(),
        compass_reader=comp,
        frame_grabber=(None if comp is None else (lambda: object())),
        widget_ring_on_miss=on_miss,
        record=lambda t, p: logged.append((t, p)),
    )
    if status is not None:
        kw["status_supplier"] = status
    return StepContext(**kw)


# AC1 — widget never found + compass off-centre: the step ACTS and exits BOUNDED
# via WidgetRingAcquireExhausted, never the 18s WidgetRingTimeout dead-wait.
def test_ac1_acquires_and_exits_bounded_not_timeout():
    logged = []
    sender = FakeSender()
    ring = _FakeRingReader([_read(0, 0, found=False)])   # widget never found
    comp = _FakeCompassReader([_cread(0.0, 0.30)])       # dot ABOVE centre
    ctx = _acq_ctx(sender, ring, comp, on_miss="degrade",
                   clock=_Clock(step=1.0), logged=logged)
    # timeout_s huge so ONLY the acquire cap can end it; it must still finish fast.
    assert step_orient_widget_ring(ctx, timeout_s=999.0, samples=1,
                                   acquire_max_iters=5) is True   # degrade miss
    assert "PitchUpButton" in sender.actions()   # AC2: compass conv oy>0 -> pitch UP
    assert any(t == "WidgetRingAcquireExhausted" for t, _ in logged)   # bounded
    assert not any(t == "WidgetRingTimeout" for t, _ in logged)        # no dead-wait
    acq = [p for t, p in logged if t == "WidgetRingAcquire"]
    assert len(acq) == 5                          # exactly acquire_max_iters beats


# AC2 — the acquisition press follows the COMPASS convention (offset_x>0 -> yaw
# right), NOT the inverted widget convention.
def test_ac2_acquisition_yaws_right_on_positive_offset_x():
    logged = []
    sender = FakeSender()
    ring = _FakeRingReader([_read(0, 0, found=False)])
    comp = _FakeCompassReader([_cread(0.30, 0.0)])       # dot RIGHT of centre
    ctx = _acq_ctx(sender, ring, comp, on_miss="degrade",
                   clock=_Clock(step=1.0), logged=logged)
    step_orient_widget_ring(ctx, timeout_s=999.0, samples=1, acquire_max_iters=3)
    assert "YawRightButton" in sender.actions()


# AC3 — acquire (compass axis) for K not-found beats, then found-off-axis
# fine-tune (widget axis), then aligned.
def test_ac3_acquire_then_fine_tune_then_aligned():
    logged = []
    sender = FakeSender()
    ring = _FakeRingReader([_read(0, 0, found=False), _read(0, 0, found=False),
                            _read(80, 0), _read(0, 0)])
    comp = _FakeCompassReader([_cread(0.0, 0.30)])       # acquire -> PitchUp
    ctx = _acq_ctx(sender, ring, comp, on_miss="fail_closed",
                   clock=_Clock(), logged=logged)
    assert step_orient_widget_ring(ctx, samples=1) is True
    acts = sender.actions()
    assert "PitchUpButton" in acts               # acquisition phase (compass oy>0)
    assert "YawRightButton" in acts              # fine-tune phase (widget dx>0)
    assert any(t == "WidgetRingAcquire" for t, _ in logged)
    assert any(t == "WidgetRingAligned" for t, _ in logged)


# AC4 — both widget AND compass blind: ZERO presses (no blind search), bounded
# miss per policy.
def test_ac4_no_blind_press_when_both_blind():
    logged = []
    sender = FakeSender()
    ring = _FakeRingReader([_read(0, 0, found=False)])
    comp = _FakeCompassReader([CompassRead.not_found()])
    ctx = _acq_ctx(sender, ring, comp, on_miss="fail_closed",
                   clock=_Clock(step=1.0), logged=logged)
    assert step_orient_widget_ring(ctx, timeout_s=999.0, samples=1,
                                   acquire_max_iters=6) is False    # fail_closed
    assert sender.actions() == []                # NO blind press
    assert any(t == "WidgetRingAcquireExhausted" for t, _ in logged)
    assert not any(t == "WidgetRingTimeout" for t, _ in logged)


# AC5 — losing supercruise during acquisition fails CLOSED even under degrade.
def test_ac5_sc_loss_during_acquire_fails_closed():
    logged = []
    sender = FakeSender()
    ring = _FakeRingReader([_read(0, 0, found=False)])   # never found -> acquiring
    comp = _FakeCompassReader([_cread(0.0, 0.30)])
    ctx = _acq_ctx(sender, ring, comp, on_miss="degrade",
                   clock=_Clock(), logged=logged, status=_FlipStatus(n_true=2))
    assert step_orient_widget_ring(ctx, timeout_s=999, samples=1) is False
    assert any(t == "WidgetRingAbort" and p.get("why") == "supercruise_lost"
               for t, p in logged)


# AC6 — widget not-found + compass UNWIRED: bounded miss, zero presses, no
# dead-wait.
def test_ac6_compass_unwired_bounded_miss():
    logged = []
    sender = FakeSender()
    ring = _FakeRingReader([_read(0, 0, found=False)])
    ctx = _acq_ctx(sender, ring, None, on_miss="degrade",   # comp None -> grabber None
                   clock=_Clock(step=1.0), logged=logged)
    assert step_orient_widget_ring(ctx, timeout_s=999.0, samples=1,
                                   acquire_max_iters=7) is True
    assert sender.actions() == []
    assert not any(t == "WidgetRingTimeout" for t, _ in logged)
    assert any(t == "WidgetRingAcquireExhausted" for t, _ in logged)


# AC7 — the widget-found-from-start path is byte-for-byte unchanged and NEVER
# consults the compass.
def test_ac7_found_from_start_never_consults_compass():
    logged = []
    sender = FakeSender()
    ring = _FakeRingReader([_read(80, 0), _read(0, 0)])
    comp = _FakeCompassReader([_cread(0.0, 0.30)])
    ctx = _acq_ctx(sender, ring, comp, on_miss="fail_closed",
                   clock=_Clock(), logged=logged)
    assert step_orient_widget_ring(ctx, samples=1) is True
    assert comp.calls == 0                        # never needed the compass
    assert sender.actions() == ["YawRightButton"] # identical to pre-fix
    assert not any(t == "WidgetRingAcquire" for t, _ in logged)


# AC9 — acquire_deadzone is strictly < orient_compass's coarse align_tol, so a
# residual the coarse stage already blessed as "aligned" is still pressable.
def test_ac9_gap_inside_coarse_deadzone_still_pressable():
    from ed_core.config import VisionConfig
    assert 0.05 < VisionConfig().align_tol       # acquire_deadzone < coarse gate
    logged = []
    sender = FakeSender()
    ring = _FakeRingReader([_read(0, 0, found=False)])
    comp = _FakeCompassReader([_cread(0.0, 0.10)])   # inside align_tol 0.20, outside 0.05
    ctx = _acq_ctx(sender, ring, comp, on_miss="degrade",
                   clock=_Clock(step=1.0), logged=logged)
    step_orient_widget_ring(ctx, timeout_s=999.0, samples=1,
                            acquire_max_iters=4, acquire_deadzone=0.05)
    assert "PitchUpButton" in sender.actions()    # pressed despite tiny residual
