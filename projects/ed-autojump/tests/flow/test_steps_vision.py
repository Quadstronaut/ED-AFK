from ed_autojump.flow.context import StepContext
from ed_autojump.flow.steps import STEP_REGISTRY
from tests.flow import FakeSender
from ed_autojump.vision.compass import CompassRead


class FakeReader:
    """Returns a queued sequence of CompassReads, one per .read() call."""
    def __init__(self, reads):
        self._reads = list(reads)
    def read(self, frame):
        return self._reads.pop(0) if self._reads else CompassRead.not_found()


def _ctx(reader):
    sender = FakeSender()
    return StepContext(
        sender=sender,
        sleeper=lambda s: None,
        compass_reader=reader,
        frame_grabber=lambda: object(),   # any non-None frame
        compass_samples=1,                 # 1 read per measurement in tests
    ), sender


def _ahead(y):  # filled dot at vertical offset y
    return CompassRead(found=True, offset_x=0.0, offset_y=y, in_front=True, confidence=1.0)


def _behind():  # hollow dot near centre = directly astern
    return CompassRead(found=True, offset_x=0.0, offset_y=0.0, in_front=False, confidence=1.0)


def test_pitch_compass_edge_stops_when_dot_reaches_rim():
    # centred -> pitch -> near rim (magnitude >= edge_frac)
    reader = FakeReader([_ahead(0.0), _ahead(-0.7)])
    ctx, sender = _ctx(reader)
    ok = STEP_REGISTRY["pitch_compass"](ctx, until="edge", edge_frac=0.6,
                                        pitch_hold=1.0, settle_s=0.0,
                                        max_iters=5, timeout_s=999)
    assert ok is True
    assert sender.actions() == ["PitchUpButton"]   # one pitch got it to the rim


def test_pitch_compass_behind_stops_on_hollow_centre():
    reader = FakeReader([_ahead(-0.7), _behind()])
    ctx, sender = _ctx(reader)
    ok = STEP_REGISTRY["pitch_compass"](ctx, until="behind", center_frac=0.25,
                                        pitch_hold=1.0, settle_s=0.0,
                                        max_iters=5, timeout_s=999)
    assert ok is True


def test_pitch_compass_fails_closed_without_vision():
    ctx = StepContext(sender=FakeSender())   # no reader/grabber
    assert STEP_REGISTRY["pitch_compass"](ctx, until="edge") is False


def test_orient_compass_fails_closed_without_vision():
    ctx = StepContext(sender=FakeSender())
    assert STEP_REGISTRY["orient_compass"](ctx) is False


def test_orient_compass_returns_alignment_result():
    reader = FakeReader([_ahead(0.0)])             # already centred -> aligned
    ctx, _ = _ctx(reader)
    # tight tol so a centred dot counts as aligned in one measure
    assert STEP_REGISTRY["orient_compass"](ctx, align_tol=0.2, max_iters=2, timeout_s=999) is True


def test_orient_compass_logs_per_iteration_telemetry():
    """Every align iteration lands in the session recording as OrientIter â€”
    reads were invisible in session_123734 and the oscillation could only be
    root-caused because ED happened to still be running (2026-06-06)."""
    reader = FakeReader([_ahead(-0.6), _ahead(0.0)])   # one press, then aligned
    ctx, _ = _ctx(reader)
    logged = []
    ctx.record = lambda kind, payload: logged.append((kind, payload))
    assert STEP_REGISTRY["orient_compass"](ctx, align_tol=0.2, max_iters=5, timeout_s=999) is True
    iters = [p for k, p in logged if k == "OrientIter"]
    assert len(iters) == 2
    assert iters[0]["action"] == "PitchDownButton"     # dot low -> pitch down
    assert iters[1]["aligned"] is True


def test_orient_compass_dumps_frames_when_sink_wired():
    reader = FakeReader([_ahead(0.0)])
    ctx, _ = _ctx(reader)
    saved = []
    ctx.frame_sink = lambda name, frame: saved.append(name)
    assert STEP_REGISTRY["orient_compass"](ctx, align_tol=0.2, max_iters=2, timeout_s=999) is True
    assert len(saved) == 1                              # 1 iter x samples=1
    assert "i00" in saved[0]                            # name carries iteration


def test_orient_compass_no_sink_no_crash():
    """frame_sink is optional â€” None must not break the align loop."""
    reader = FakeReader([_ahead(0.0)])
    ctx, _ = _ctx(reader)
    assert ctx.frame_sink is None
    assert STEP_REGISTRY["orient_compass"](ctx, align_tol=0.2, max_iters=2, timeout_s=999) is True


class _FlipStatus:
    """Status supplier: in_supercruise True for the first `n_true` reads,
    then False (the 13:26 emergency drop, mid-orient)."""

    def __init__(self, n_true):
        self.n = n_true
        self.calls = 0

    def __call__(self):
        self.calls += 1
        from types import SimpleNamespace
        return SimpleNamespace(in_supercruise=self.calls <= self.n)


def test_orient_compass_fails_closed_when_supercruise_lost():
    """2026-06-06 13:26: SupercruiseExit at the star 10s into orient; the loop
    steered normal-space glare garbage for 35 more seconds. A step that began
    in supercruise must DIE the moment the flag drops, not at timeout."""
    reader = FakeReader([_ahead(-0.6)] * 50)   # never aligns on its own
    ctx, sender = _ctx(reader)
    logged = []
    ctx.record = lambda kind, payload: logged.append((kind, payload))
    ctx.status_supplier = _FlipStatus(n_true=2)  # in SC at start, drops fast
    ok = STEP_REGISTRY["orient_compass"](ctx, align_tol=0.2, max_iters=50,
                                         timeout_s=999, settle_s=0.0)
    assert ok is False
    orient = [p for k, p in logged if k == "Orient"][-1]
    assert orient["reason"] == "supercruise_lost"
    assert len(sender.actions()) <= 3            # stopped pressing immediately


def test_orient_compass_guard_inert_when_starting_in_normal_space():
    """smack_recovery's escape-vector orient runs in NORMAL space (during the
    SC charge). The guard arms only when the step STARTS in supercruise â€”
    a normal-space start must orient exactly as before."""
    reader = FakeReader([_ahead(-0.6), _ahead(0.0)])   # one press, then aligned
    ctx, _ = _ctx(reader)
    ctx.status_supplier = lambda: __import__("types").SimpleNamespace(
        in_supercruise=False)
    assert STEP_REGISTRY["orient_compass"](ctx, align_tol=0.2, max_iters=5,
                                           timeout_s=999, settle_s=0.0) is True


def _behind_at(ox, oy):
    return CompassRead(found=True, offset_x=ox, offset_y=oy, in_front=False,
                       confidence=1.0)


def test_pitch_behind_far_left_yaws_right():
    """2026-06-06 13:45 spin loop: smack_recovery's pitch_compass pressed
    PitchUp 25x with the star behind at ox=-0.86 -- pitch moves the dot
    VERTICALLY and can never close a horizontal offset, so the 'behind +
    mag<=0.25' gate was unreachable and the ship looped forever. Behind-
    hemisphere dynamics are MIRRORED: dot left (ox<0) -> YawRight drives it
    to centre while staying behind."""
    reader = FakeReader([_behind_at(-0.9, 0.2), _behind_at(-0.1, 0.1)])
    ctx, sender = _ctx(reader)
    ok = STEP_REGISTRY["pitch_compass"](ctx, until="behind", center_frac=0.25,
                                        pitch_hold=1.0, settle_s=0.0,
                                        max_iters=5, timeout_s=999)
    assert ok is True
    assert sender.actions() == ["YawRightButton"]


def test_pitch_behind_far_right_yaws_left():
    reader = FakeReader([_behind_at(0.9, 0.2), _behind_at(0.1, 0.1)])
    ctx, sender = _ctx(reader)
    ok = STEP_REGISTRY["pitch_compass"](ctx, until="behind", center_frac=0.25,
                                        pitch_hold=1.0, settle_s=0.0,
                                        max_iters=5, timeout_s=999)
    assert ok is True
    assert sender.actions() == ["YawLeftButton"]


def test_pitch_behind_high_pitches_down():
    # behind + dot above centre: PitchDown reduces behind-oy (mirrored law)
    reader = FakeReader([_behind_at(0.05, 0.6), _behind_at(0.05, 0.1)])
    ctx, sender = _ctx(reader)
    ok = STEP_REGISTRY["pitch_compass"](ctx, until="behind", center_frac=0.25,
                                        pitch_hold=1.0, settle_s=0.0,
                                        max_iters=5, timeout_s=999)
    assert ok is True
    assert sender.actions() == ["PitchDownButton"]


def test_pitch_behind_low_pitches_up():
    reader = FakeReader([_behind_at(0.05, -0.6), _behind_at(0.05, -0.1)])
    ctx, sender = _ctx(reader)
    ok = STEP_REGISTRY["pitch_compass"](ctx, until="behind", center_frac=0.25,
                                        pitch_hold=1.0, settle_s=0.0,
                                        max_iters=5, timeout_s=999)
    assert ok is True
    assert sender.actions() == ["PitchUpButton"]


def test_pitch_compass_logs_per_iteration_telemetry():
    """The 13:45 spin was 25 opaque presses -- pitch_compass now logs
    PitchIter rows like orient does."""
    reader = FakeReader([_ahead(0.0), _ahead(-0.7)])
    ctx, _ = _ctx(reader)
    logged = []
    ctx.record = lambda kind, payload: logged.append((kind, payload))
    STEP_REGISTRY["pitch_compass"](ctx, until="edge", edge_frac=0.6,
                                   pitch_hold=1.0, settle_s=0.0,
                                   max_iters=5, timeout_s=999)
    iters = [p for k, p in logged if k == "PitchIter"]
    assert len(iters) == 2
    assert iters[0]["action"] == "PitchUpButton"
    assert iters[1]["action"] is None            # gate reached, no press


def test_pitch_behind_centering_uses_proportional_taps():
    """2026-06-06 13:53 (session_135247, PitchIter): the dot sat at behind
    mag 0.39 -- 4/100ths past the 0.25 gate -- and every 1.0s press rotated
    the ship ~110+ degrees (the behind->front flip at i3->i4 proves >=107 by
    geometry), blasting through the +/-center_frac window in a deterministic
    2-cycle. Behind-centering presses must be PROPORTIONAL taps (gain*mag,
    floored), never the full flip-power pitch_hold."""
    reader = FakeReader([_behind_at(-0.4, 0.1), _behind_at(-0.1, 0.05)])
    ctx, sender = _ctx(reader)
    ok = STEP_REGISTRY["pitch_compass"](ctx, until="behind", center_frac=0.25,
                                        pitch_hold=1.0, settle_s=0.0,
                                        max_iters=5, timeout_s=999)
    assert ok is True
    action, hold = sender.holds[0]
    assert action == "YawRightButton"
    assert hold < 0.3                      # a tap, nowhere near pitch_hold
    assert hold >= 0.08                    # but above the actuation floor


def test_pitch_front_flip_keeps_full_power():
    """The front->behind flip phase NEEDS the big press (~140 deg traverse);
    only the behind-centering phase gets taps."""
    reader = FakeReader([_ahead(-0.7), _behind_at(0.05, 0.1)])
    ctx, sender = _ctx(reader)
    ok = STEP_REGISTRY["pitch_compass"](ctx, until="behind", center_frac=0.25,
                                        pitch_hold=1.0, settle_s=0.0,
                                        max_iters=5, timeout_s=999)
    assert ok is True
    action, hold = sender.holds[0]
    assert action == "PitchUpButton"
    assert hold == 1.0                     # full pitch_hold for the flip



def test_nav_panel_target_verifies_lock_via_compass():
    """2026-06-06 14:07 (run 4): target_via_navpanel is a blind TOGGLE -- on
    an already-locked star the second UI_Select hits UNLOCK, the compass
    hologram vanishes, and pitch_compass read found=False 31x. Proven live
    14:16: one macro run re-locked it and the dot appeared instantly. The
    step must VERIFY the lock (compass dot found) and re-toggle if absent."""
    reader = FakeReader([_ahead(0.3)])     # dot visible after first macro
    ctx, sender = _ctx(reader)
    ok = STEP_REGISTRY["nav_panel_target"](ctx, settle_s=0.0)
    assert ok is True
    assert sender.events.count("FocusLeftPanel") == 2   # exactly one macro


def test_nav_panel_target_retoggles_when_no_dot():
    """First macro lands on the unlock side of the toggle -> no dot -> run
    the macro again; dot appears -> True."""
    reader = FakeReader([CompassRead.not_found(), CompassRead.not_found(),
                         CompassRead.not_found(), _ahead(0.3)])
    ctx, sender = _ctx(reader)
    ok = STEP_REGISTRY["nav_panel_target"](ctx, settle_s=0.0, verify_reads=3)
    assert ok is True
    assert sender.events.count("FocusLeftPanel") == 4   # two macro runs


def test_nav_panel_target_fails_closed_when_dot_never_appears():
    """Glare or true vision loss: never seen -> False (required step ->
    procedure retry), NOT a blind True that sends pitch hunting nothing."""
    reader = FakeReader([])                # always not_found
    ctx, sender = _ctx(reader)
    ok = STEP_REGISTRY["nav_panel_target"](ctx, settle_s=0.0, verify_reads=2,
                                           max_toggles=2)
    assert ok is False
    assert sender.events.count("FocusLeftPanel") == 4   # capped at 2 macros


def test_nav_panel_target_blind_without_vision():
    """No compass wired -> original blind behavior (macro once, True)."""
    sender = FakeSender()
    ctx = StepContext(sender=sender, sleeper=lambda s: None)
    ok = STEP_REGISTRY["nav_panel_target"](ctx, settle_s=0.0)
    assert ok is True
    assert sender.events.count("FocusLeftPanel") == 2


def test_pitch_transient_not_found_holds_position():
    """Run 5 (session_142245): single not_found beats (glare flicker) fired
    the full-power 1.0s blind sweep and WRECKED converging poses (i33 mag
    0.41 -> not_found -> 1.0s blast -> pose gone). One missed beat must
    press NOTHING; only k consecutive misses justify the sweep."""
    reader = FakeReader([_behind_at(-0.3, -0.1), CompassRead.not_found(),
                         _behind_at(-0.1, -0.05)])
    ctx, sender = _ctx(reader)
    ok = STEP_REGISTRY["pitch_compass"](ctx, until="behind", center_frac=0.25,
                                        pitch_hold=1.0, settle_s=0.0,
                                        max_iters=6, timeout_s=999)
    assert ok is True
    # one yaw tap for the first read, NOTHING for the transient miss
    assert sender.actions() == ["YawRightButton"]


def test_pitch_consecutive_not_found_sweeps():
    """3 consecutive misses = the dot is genuinely not visible -> blind
    sweep resumes (the original search behavior)."""
    reader = FakeReader([CompassRead.not_found()] * 3 + [_behind_at(-0.1, -0.05)])
    ctx, sender = _ctx(reader)
    ok = STEP_REGISTRY["pitch_compass"](ctx, until="behind", center_frac=0.25,
                                        pitch_hold=1.0, settle_s=0.0,
                                        max_iters=8, timeout_s=999)
    assert ok is True
    assert sender.holds[0] == ("PitchUpButton", 1.0)    # sweep on miss #3
    assert len(sender.actions()) == 1                   # misses 1-2 pressed nothing
