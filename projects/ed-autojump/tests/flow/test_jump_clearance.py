"""step_engage_jump_clearance charge-aware poll loop (LIVE FIX 2026-07-06)
and the D3 obstructed-edge SC-assist ORBIT get-around (2026-07-07 council).

Run 004703 sabotage, pinned: the ~15s hyperspace spool shows NEITHER commit
signal (witchspace / fsd_jump bit 30), so the original 12-poll x 0.8s window
verdicted every HEALTHY jump "obscured" and the C4 move pitched the ship off
its own live charge. fsd_charging is the took-signal now:

  no charge within max_jump_polls  -> ED refused the press -> C4 move edge
  charging                         -> wait for commit (max_charge_polls ceiling)
  charge drops without commit      -> grace poll -> C4 move edge
  charge outlives the ceiling      -> False (outer retry re-orients; NEVER
                                     pitch off a live charge)

D3 (2026-07-07): the C4 move edge no longer pitches-and-forward-burns (which
could ram a jump target hidden BEHIND the star). It engages an SC-assist
ORBIT get-around (executor.navpanel.engage_supercruise_assist: FocusLeftPanel
-> UI_Select -> UI_Right -> UI_Select -> FocusLeftPanel) to change angular
position instead, and NEVER presses SetSpeed100 at that edge. THROTTLE
FAIL-CLOSED: not in supercruise -> bail with no throttle at all.

Pure-Python — no game, no CV. The rig advances its FSD phase on each SLEEP
(one sleep per poll), because the step reads status twice per poll (commit
check + charge check) and a per-call script would desync.
"""

from types import SimpleNamespace

from ed_core.flow.context import StepContext
from ed_autojump.flow.steps import STEP_REGISTRY
from tests.flow import FakeSender

# The orbit get-around's exact key sequence (engage_supercruise_assist).
GETAROUND = ["FocusLeftPanel", "UI_Select", "UI_Right", "UI_Select", "FocusLeftPanel"]


class _FsdRig:
    """FSD phase scripted per POLL: 'idle' / 'charging' / 'jump'.

    The sleeper is the poll clock — each ctx.sleeper() call advances one
    phase slot; the final phase holds forever. in_supercruise=True always:
    engage_jump_clearance only ever runs while flying (the ONE structural
    precondition the D3 SC-assist orbit get-around needs)."""

    def __init__(self, phases):
        self.phases = list(phases)
        self.polls = 0

    def sleep(self, _s):
        self.polls += 1

    def status(self):
        phase = self.phases[min(self.polls, len(self.phases) - 1)]
        return SimpleNamespace(
            docked=False, in_supercruise=True,
            fsd_charging=(phase == "charging"),
            fsd_cooldown=False, fsd_mass_locked=False, overheating=False,
            fsd_jump=(phase == "jump"),
        )


def _ctx(sender, phases, logs=None):
    rig = _FsdRig(phases)
    return StepContext(
        sender=sender,
        sleeper=rig.sleep,
        status_supplier=rig.status,
        ship_supplier=lambda: "mandalay",
        record=(lambda kind, payload: logs.append((kind, payload)))
        if logs is not None else None,
    )


def test_healthy_charge_longer_than_old_window_commits():
    """THE 2026-07-06 REGRESSION: a healthy ~15s spool (19 charging polls,
    far past the old 12-poll verdict) must be WAITED OUT to the commit —
    no pitch, no extra presses, one attempt."""
    sender = FakeSender()
    ctx = _ctx(sender, ["idle"] + ["charging"] * 19 + ["jump"])
    assert STEP_REGISTRY["engage_jump_clearance"](ctx) is True
    assert sender.actions() == ["SetSpeed100", "Hyperspace"]   # no move ever


def test_slow_live_charge_past_old_default_still_commits():
    """Phroea IB-N d7-8 ram (2026-07-12): a struggling drive spooled ~72s (90
    charging polls, past the old 60s / 75-poll give-up) then committed. A
    STILL-LIVE charge is a PENDING jump, not a stuck one -- it must be waited
    out to the commit, never surrendered (charge_stuck) into the
    on_required_fail throttle-retry that re-issued SetSpeed100 into the
    just-arrived star. One attempt, no getaround, no re-press."""
    sender = FakeSender()
    ctx = _ctx(sender, ["idle"] + ["charging"] * 90 + ["jump"])
    assert STEP_REGISTRY["engage_jump_clearance"](ctx) is True
    assert sender.actions() == ["SetSpeed100", "Hyperspace"]   # no move, no retry


def test_no_charge_is_the_obstructed_edge():
    """Press refused (no charge ever) -> C4 move (D3: SC-assist orbit
    get-around, NOT a pitch+burn) then re-press; ceiling after
    max_clear_attempts -> False. NO SetSpeed100 beyond the two C1 jump
    presses -- the get-around itself never throttles."""
    sender = FakeSender()
    logs = []
    ctx = _ctx(sender, ["idle"], logs=logs)
    assert STEP_REGISTRY["engage_jump_clearance"](
        ctx, max_jump_polls=3, max_clear_attempts=2) is False
    acts = sender.actions()
    assert acts.count("Hyperspace") == 2            # re-pressed once per attempt
    assert acts.count("UI_Right") == 2              # orbit get-around ran after each refusal
    assert acts.count("SetSpeed100") == 2            # ONLY the two C1 jump presses
    assert "PitchDownButton" not in acts             # D3: never pitches
    assert ("EngageJumpClearanceAborted",
            {"reason": "obstruction_ceiling", "attempts": 2}) in logs


def test_charge_stuck_fails_without_getaround():
    """ALIGN hold / wedged FSD: charge outlives the ceiling -> False with
    reason charge_stuck and NO get-around press — never move off a live
    charge (the outer retry re-orients instead)."""
    sender = FakeSender()
    logs = []
    ctx = _ctx(sender, ["idle"] + ["charging"] * 500, logs=logs)
    assert STEP_REGISTRY["engage_jump_clearance"](
        ctx, max_charge_polls=10) is False
    assert "UI_Right" not in sender.actions()
    assert "PitchDownButton" not in sender.actions()
    assert sender.actions().count("Hyperspace") == 1
    assert sender.actions().count("SetSpeed100") == 1
    assert any(k == "EngageJumpClearanceAborted"
               and p.get("reason") == "charge_stuck" for k, p in logs)


def test_align_warning_triggers_realign_holding_charge(monkeypatch):
    """Operator ruling 2026-07-12 (Phroea NO-Q e5-9, 'not aligning after
    engaging jump'): a LIVE charge that won't commit because the ship is
    off-target shows ALIGN WITH TARGET DESTINATION (the widget-ring degraded ->
    the ship engaged on the loose coarse tol). engage must re-align to the
    target IN PLACE (compass, tighter tol) while HOLDING the charge -- NO orbit
    get-around (nothing to orbit in deep space), NO jump re-press (that cancels
    a live charge) -- and commit once aligned."""
    import ed_autojump.flow.steps as steps_mod  # noqa: F401
    import ed_vision.hud_sc_indicators as hud_mod
    import ed_core.executor.align as align_mod

    sender = FakeSender()
    state = {"committed": False}
    rig = _FsdRig(["idle"] + ["charging"] * 200)   # charge stays live
    monkeypatch.setattr(hud_mod, "detect_align_warning", lambda frame, **k: True)

    called = {"n": 0}

    def fake_align(reader, sender_, *, capture, abort_check=None, **kw):
        called["n"] += 1
        state["committed"] = True        # aligned -> the held charge commits
        return SimpleNamespace(aligned=True, iterations=3, reason="aligned")

    monkeypatch.setattr(align_mod, "align_to_target", fake_align)

    ctx = StepContext(
        sender=sender, sleeper=rig.sleep,
        status_supplier=rig.status,
        in_witchspace=lambda: state["committed"],   # commit signal
        hud_grabber=lambda: object(),
        compass_reader=object(),
        frame_grabber=lambda: object(),
        ship_supplier=lambda: "mandalay",
    )
    assert STEP_REGISTRY["engage_jump_clearance"](ctx) is True
    assert called["n"] >= 1                            # re-aligned in place
    assert "UI_Right" not in sender.actions()          # NO orbit get-around
    assert sender.actions().count("Hyperspace") == 1   # charge HELD, not re-pressed
    assert "SetSpeedZero" not in sender.actions()      # throttle never dropped


def test_no_align_warning_does_not_realign(monkeypatch):
    """HUD reads NONE (a genuine slow-but-aligned charge, not off-target): no
    re-align, the charge is simply waited out to the commit (regression guard so
    the ALIGN gate only fires on the ALIGN prompt)."""
    import ed_vision.hud_sc_indicators as hud_mod
    import ed_core.executor.align as align_mod

    sender = FakeSender()
    monkeypatch.setattr(hud_mod, "detect_align_warning", lambda frame, **k: False)
    called = {"n": 0}
    monkeypatch.setattr(align_mod, "align_to_target",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    # charge 90 polls (past the ChargeSlow line) then commit -- must be waited
    # out, never re-aligned.
    ctx = _ctx(sender, ["idle"] + ["charging"] * 90 + ["jump"])
    ctx.hud_grabber = lambda: object()
    ctx.compass_reader = object()
    ctx.frame_grabber = lambda: object()
    assert STEP_REGISTRY["engage_jump_clearance"](ctx) is True
    assert called["n"] == 0                             # never re-aligned


def test_sco_malfunction_skips_orbit_and_represses(monkeypatch):
    """Operator 2026-07-12 (FSD (SCO) MALFUNCTIONED frame): a no-charge edge with
    the malfunction prompt on the HUD is the DEVICE-DAMAGED drive briefly
    refusing to spool -- NOT an obstruction. engage must NOT orbit-assist a body
    that isn't in the way (we are already oriented to the next jump); it waits a
    recovery beat and re-presses the SAME jump each attempt."""
    import ed_vision.hud_sc_indicators as hud_mod
    monkeypatch.setattr(hud_mod, "detect_sco_malfunction", lambda frame, **k: True)
    sender = FakeSender()
    ctx = _ctx(sender, ["idle"])          # no charge ever -> obscured edge
    ctx.hud_grabber = lambda: object()
    assert STEP_REGISTRY["engage_jump_clearance"](
        ctx, max_jump_polls=2, max_clear_attempts=3,
        malfunction_recovery_s=0.0) is False
    acts = sender.actions()
    assert "UI_Right" not in acts          # NO orbit get-around
    assert acts.count("Hyperspace") == 3   # re-pressed each attempt (no orbit between)
    assert acts.count("SetSpeed100") == 3


def test_no_malfunction_still_orbits_on_obscured(monkeypatch):
    """Regression guard: with NO malfunction prompt (a real obstruction), the
    no-charge edge still runs the SC-assist orbit get-around as before."""
    import ed_vision.hud_sc_indicators as hud_mod
    monkeypatch.setattr(hud_mod, "detect_sco_malfunction", lambda frame, **k: False)
    sender = FakeSender()
    ctx = _ctx(sender, ["idle"])
    ctx.hud_grabber = lambda: object()
    STEP_REGISTRY["engage_jump_clearance"](
        ctx, max_jump_polls=2, max_clear_attempts=1)
    assert "UI_Right" in sender.actions()  # orbit get-around still fires


def test_charge_dropped_goes_to_move_edge():
    """Charge seen then dropped without commit -> grace poll -> C4 move
    (D3: SC-assist orbit get-around)."""
    sender = FakeSender()
    logs = []
    ctx = _ctx(sender, ["idle"] + ["charging"] * 4 + ["idle"] * 500, logs=logs)
    assert STEP_REGISTRY["engage_jump_clearance"](
        ctx, max_clear_attempts=1) is False
    assert "UI_Right" in sender.actions()
    assert "PitchDownButton" not in sender.actions()
    assert sender.actions().count("SetSpeed100") == 1   # only the C1 jump press
    assert any(k == "EngageJumpClearanceChargeDropped" for k, _ in logs)


class _SpaceRig(_FsdRig):
    """_FsdRig plus scripted in_supercruise: False until `sc_at` sleeps have
    elapsed (None = never). Models the G19 realspace-obscured start."""

    def __init__(self, phases, sc_at=None):
        super().__init__(phases)
        self.sc_at = sc_at

    def status(self):
        st = super().status()
        st.in_supercruise = self.sc_at is not None and self.polls >= self.sc_at
        return st


def _rs_ctx(sender, phases, sc_at=None, logs=None):
    rig = _SpaceRig(phases, sc_at)
    return StepContext(
        sender=sender,
        sleeper=rig.sleep,
        status_supplier=rig.status,
        ship_supplier=lambda: "mandalay",
        record=(lambda kind, payload: logs.append((kind, payload)))
        if logs is not None else None,
    )


def test_realspace_obscured_enters_sc_then_orbits_then_jumps():
    """G19 (operator order 2026-07-11, session 090913): a REALSPACE obscured
    jump no longer dead-ends. The step enters supercruise FIRST (throttle is
    already 100 from C1's own press — no new throttle press), then runs the
    same SC-assist ORBIT get-around, then retries the jump. The exact
    sequence of the live-validated startup CLOSE lane."""
    sender = FakeSender()
    logs = []
    # attempt 1: idle x2 -> obscured; SC entry completes at 2 sleeps; orbit;
    # settle; attempt 2 commits.
    ctx = _rs_ctx(sender, ["idle", "idle", "idle", "jump"], sc_at=2, logs=logs)
    assert STEP_REGISTRY["engage_jump_clearance"](
        ctx, max_jump_polls=2, max_clear_attempts=2) is True
    acts = sender.actions()
    assert acts.count("Supercruise") == 1           # entered SC exactly once
    assert acts.count("UI_Right") == 1              # orbit get-around ran once
    assert acts.count("Hyperspace") == 2            # re-pressed after the move
    assert acts.count("SetSpeed100") == 2           # ONLY the two C1 presses
    assert "PitchDownButton" not in acts            # D3 stands: never pitches
    assert any(k == "EngageJumpClearanceScEntry" for k, _ in logs)
    # ordering: SC entry strictly before the orbit macro
    assert acts.index("Supercruise") < acts.index("UI_Right")


def test_realspace_sc_entry_failure_bails_with_no_orbit_press():
    """G19 FAIL-CLOSED: entry refused / never completes (exclusion zone,
    cooldown, wedged FSD) -> bail with ZERO orbit presses and no further
    throttle — the required-fail routes to never-strand / smack dispatch."""
    sender = FakeSender()
    logs = []
    ctx = _rs_ctx(sender, ["idle"], sc_at=None, logs=logs)   # SC never happens
    assert STEP_REGISTRY["engage_jump_clearance"](
        ctx, max_jump_polls=2, max_clear_attempts=3,
        max_sc_entry_polls=3) is False
    acts = sender.actions()
    assert "UI_Right" not in acts and "UI_Select" not in acts
    assert acts.count("Supercruise") == 1        # one entry attempt, no spam
    assert acts.count("SetSpeed100") == 1        # only the single C1 attempt
    assert acts.count("Hyperspace") == 1         # bailed before any retry
    assert any(k == "EngageJumpClearanceAborted"
               and p.get("reason") == "getaround_unavailable"
               and p.get("cause") == "sc_entry_failed"
               for k, p in logs)


def test_getaround_dropped_mid_macro_aborts(monkeypatch):
    """A mid-macro emergency drop (out of supercruise) during the get-around
    means the smack dispatch owns the scene -- abort, do not retry blind."""
    # step_engage_jump_clearance imports FROM ed_autojump.executor.navpanel
    # (its `from ..executor.navpanel import ...`), which itself re-exports
    # ed_core's implementation via `from ed_core.executor.navpanel import *`
    # -- a NAME-BINDING COPY at ed_autojump.executor.navpanel's own import
    # time, not a live alias. Patch THAT module (the one the step actually
    # resolves at call time), not ed_core's.
    import ed_autojump.executor.navpanel as navpanel
    sender = FakeSender()
    logs = []
    ctx = _ctx(sender, ["idle"], logs=logs)
    dropped = {"v": False}
    real_status = ctx.status_supplier

    def _status():
        if dropped["v"]:
            return SimpleNamespace(
                docked=False, in_supercruise=False, fsd_charging=False,
                fsd_cooldown=False, fsd_mass_locked=False, overheating=False,
                fsd_jump=False)
        return real_status()
    ctx.status_supplier = _status

    def _fake_engage(sender_, **kw):
        dropped["v"] = True          # the macro "ran" and the ship dropped mid-press

    monkeypatch.setattr(navpanel, "engage_supercruise_assist", _fake_engage)
    assert STEP_REGISTRY["engage_jump_clearance"](
        ctx, max_jump_polls=2, max_clear_attempts=3) is False
    assert any(k == "EngageJumpClearanceAborted"
               and p.get("reason") == "getaround_dropped" for k, p in logs)


def test_commit_during_grace_poll_still_wins():
    """Charge drops but the commit lands on the grace poll (status-write vs
    journal-write race) -> True, no move."""
    sender = FakeSender()
    # idle -> charging x3 -> idle (drop seen) -> jump (grace poll)
    ctx = _ctx(sender, ["idle"] + ["charging"] * 3 + ["idle", "jump"])
    assert STEP_REGISTRY["engage_jump_clearance"](ctx) is True
    assert "PitchDownButton" not in sender.actions()
