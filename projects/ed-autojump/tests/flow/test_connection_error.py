"""Connection-error real-time monitor: watch-loop latching + main-thread
recovery dispatch + the recovery menu macro (operator 2026-07-12).

The CONNECTION ERROR dialog carries NO journal event, so a background CV/OCR
watch is the only signal. On a hit the watch daemon latches a preempt (aborts
the running scene) + a recovery flag; run_live's idle branch runs
connection_recovery on the MAIN thread (procedure execution stays
single-threaded -- the watch thread never presses keys). The detector itself is
tested in tests/vision/test_hud_sc_indicators.py; here we mock it and pin the
dispatcher plumbing + the operator-verified key sequence.
"""

from pathlib import Path
from types import SimpleNamespace

from ed_autojump.flow.dispatcher import FlowRunner
from ed_core.flow.context import StepContext
from ed_core.flow.model import Procedure, Step
from ed_core.flow.steps_shared import step_connection_recovery
from tests.flow import FakeSender

PROC_DIR = Path(__file__).resolve().parents[2] / "procedures"
_DETECT = "ed_vision.hud_sc_indicators.detect_connection_error"


def _runner(procs=None, sender=None):
    return FlowRunner(
        procedures=procs or {},
        sender=sender or FakeSender(),
        clock=lambda: 0.0,
        sleeper=lambda s: None,
        status_supplier=lambda: SimpleNamespace(
            docked=False, in_supercruise=True, fsd_charging=False,
            fsd_cooldown=False, fsd_mass_locked=False, overheating=False),
    )


# ---- watch tick: detection -> latches --------------------------------------

def test_connection_tick_latches_preempt_and_flag(monkeypatch):
    """A detected modal sets the recovery flag AND (a proc running) a preempt
    that aborts it -- unconditionally, no scene allow-list (a drop obsoletes
    EVERY scene, unlike star_smack)."""
    monkeypatch.setattr(_DETECT, lambda f: True)
    r = _runner()
    r._connection_grabber = lambda: object()   # any non-None frame
    r._running_proc = "smack_recovery"          # even smack_recovery is preempted
    r._connection_tick()
    assert r._connection_error_seen is True
    assert r._preempt == "connection_error"


def test_connection_tick_no_hit_no_latch(monkeypatch):
    monkeypatch.setattr(_DETECT, lambda f: False)
    r = _runner()
    r._connection_grabber = lambda: object()
    r._running_proc = "traversal"
    r._connection_tick()
    assert r._connection_error_seen is False
    assert r._preempt is None


def test_connection_tick_debounced_while_pending(monkeypatch):
    """While a recovery is already pending, the tick does NOT re-grab/re-detect
    -- one episode, one recovery."""
    calls = []
    monkeypatch.setattr(_DETECT, lambda f: calls.append(1) or True)
    r = _runner()
    r._connection_grabber = lambda: calls.append("grab") or object()
    r._connection_error_seen = True   # already pending
    r._connection_tick()
    assert calls == []                # neither grabber nor detector consulted


def test_connection_tick_fires_even_during_input_exclusive(monkeypatch):
    """COUNCIL #4: a CONNECTION ERROR can strike DURING a long input_exclusive
    engage_jump_clearance (~240s). The tick only READS a frame + latches (never
    presses keys) and a full-screen modal is not a panel frame, so it must NOT be
    gated on input_exclusive -- it fires and latches."""
    monkeypatch.setattr(_DETECT, lambda f: True)
    r = _runner()
    r._connection_grabber = lambda: object()
    r._running_proc = "traversal"
    with r._exclusive_input():
        r._connection_tick()
    assert r._connection_error_seen is True
    assert r._preempt == "connection_error"


def test_connection_tick_no_grabber_is_noop(monkeypatch):
    monkeypatch.setattr(_DETECT, lambda f: True)
    r = _runner()                      # no connection_grabber wired
    r._connection_tick()
    assert r._connection_error_seen is False


# ---- main-thread recovery consumer -----------------------------------------

def test_maybe_recover_runs_recovery_and_clears_flag():
    """The consumer runs connection_recovery (the operator key sequence) then
    clears the latch so a still-down server re-triggers next tick."""
    sender = FakeSender()
    procs = {"connection_recovery": Procedure(
        name="connection_recovery", steps=(Step("connection_recovery"),))}
    r = _runner(procs, sender)
    r._connection_error_seen = True
    r._maybe_recover_connection()
    assert r._connection_error_seen is False
    # operator-verified sequence: OK -> CONTINUE -> Solo(D,D) -> load -> replot.
    assert sender.actions() == [
        "UI_Select", "UI_Select", "UI_Right", "UI_Right",
        "UI_Select", "GalaxyMapOpen", "UI_Back"]


def test_maybe_recover_unwired_clears_flag_and_logs():
    """No connection_recovery procedure (minimal build): clear the flag + log,
    don't spin re-detecting with no consumer."""
    logs = []
    r = _runner()
    r.record = lambda k, p: logs.append((k, p))
    r._connection_error_seen = True
    r._maybe_recover_connection()
    assert r._connection_error_seen is False
    assert any(k == "ConnectionRecoveryUnwired" for k, _ in logs)


def test_maybe_recover_noop_when_not_flagged():
    sender = FakeSender()
    r = _runner(sender=sender)
    r._maybe_recover_connection()
    assert sender.actions() == []      # nothing flagged -> nothing pressed


# ---- the recovery procedure wires up ---------------------------------------

def test_connection_recovery_procedure_registered_and_valid():
    """connection_recovery.toml loads and every action resolves against the
    merged step registry (the connection_recovery step is registered)."""
    from ed_core.flow.loader import load_procedures, validate_procedure
    from ed_autojump.flow.steps import STEP_REGISTRY
    procs = load_procedures(PROC_DIR)
    assert "connection_recovery" in procs
    assert validate_procedure(
        procs["connection_recovery"], known_actions=STEP_REGISTRY.keys()) == []


# ---- council 2026-07-12 fixes: post-recovery handoff + fail-closed re-entry --

def _recovery_ctx(sender, *, load_result=True, abort=False):
    notified = []
    ctx = StepContext(
        sender=sender, sleeper=lambda s: None,
        event_waiter=(None if load_result is None else (lambda name, t: load_result)),
        should_abort=(lambda: abort),
        record=lambda k, p: None,
    )
    ctx.connection_recovery_notify = notified.append
    return ctx, notified


def test_recovery_success_arms_redispatch():
    """COUNCIL #1 (BLOCKER): a clean recovery used to strand -- classify_startup is
    one-shot and no journal route fires at a static reconnect. On success the
    consumer must ARM the never-strand re-dispatch so flight resumes."""
    sender = FakeSender()
    procs = {"connection_recovery": Procedure(
        name="connection_recovery", steps=(Step("connection_recovery"),))}
    r = _runner(procs, sender)
    r._connection_error_seen = True
    r._maybe_recover_connection()
    assert r._connection_recovered is True       # step got back in-game (no waiter -> assume)
    assert r._needs_redispatch is True           # <- the fix: handoff armed
    assert r._connection_error_seen is False


def test_step_recovery_loadgame_timeout_fails_closed():
    """COUNCIL #2 (BLOCKER): on a LoadGame timeout (Solo still grayed) the step
    used to return True + blind-press the galaxy map into the dead menu. Now it
    returns False, notifies failure, and presses NO map keys."""
    sender = FakeSender()
    ctx, notified = _recovery_ctx(sender, load_result=False)
    assert step_connection_recovery(ctx) is False
    assert notified == [False]
    assert "GalaxyMapOpen" not in sender.actions()
    assert "UI_Back" not in sender.actions()


def test_step_recovery_success_replots_and_notifies():
    sender = FakeSender()
    ctx, notified = _recovery_ctx(sender, load_result=True)
    assert step_connection_recovery(ctx) is True
    assert notified == [True]
    assert "GalaxyMapOpen" in sender.actions() and "UI_Back" in sender.actions()


def test_step_recovery_panic_aborts_early():
    """COUNCIL (panic finding): the operator panic hotkey must interrupt the
    macro -- it must NOT press its whole body into a dead client."""
    sender = FakeSender()
    ctx, notified = _recovery_ctx(sender, abort=True)
    assert step_connection_recovery(ctx) is False
    assert notified == [False]
    assert sender.actions() == []                # nothing pressed under panic


# ---- witchspace-latch release on detection (operator 2026-07-13, LIVE 031346) -

def test_connection_tick_releases_stuck_witchspace_latch(monkeypatch):
    """LIVE 2026-07-13 (session 031346): a CONNECTION ERROR mid-Hyperspace-jump
    leaves _in_witchspace stuck True (the jump never lands, so no
    FSDJump/SC-entry/Docked/Location clears it) and the interpreter then
    witchspace-pauses connection_recovery into a HARD FREEZE. Detecting the modal
    must RELEASE the latch so recovery is not gagged before its first press."""
    monkeypatch.setattr(_DETECT, lambda f: True)
    r = _runner()
    r._connection_grabber = lambda: object()
    r._in_witchspace = True           # stuck from the interrupted mid-jump
    r._running_proc = "traversal"
    r._connection_tick()
    assert r._in_witchspace is False       # released -> recovery won't be paused
    assert r._connection_error_seen is True
    assert r._preempt == "connection_error"


def test_maybe_recover_clears_witchspace_before_running(monkeypatch):
    """Belt-and-suspenders: the consumer clears the latch immediately before
    running connection_recovery, so even a backlog StartJump that re-set it after
    detection can't witchspace-pause the recovery macro (031346's dead-stop)."""
    sender = FakeSender()
    procs = {"connection_recovery": Procedure(
        name="connection_recovery", steps=(Step("connection_recovery"),))}
    r = _runner(procs, sender)
    r._connection_error_seen = True
    r._in_witchspace = True
    r._maybe_recover_connection()
    assert r._in_witchspace is False


# ---- corner-black Solo confirm + grayed-auth retry + never-fly-Open net -------
# (operator 2026-07-13 frames: press Solo, confirm the black loading screen, retry
#  while the mode cards are grayed/authenticating, and refuse a non-Solo landing.)

def _cv_ctx(sender, *, game_mode=None, load_result=None, abort=False):
    """StepContext wired with a connection_grabber + an ADVANCING clock (sleeper
    advances it) so the bounded confirm/retry windows actually elapse. event_waiter
    defaults to None (LoadGame unwired) so tests exercise the CORNER-black path;
    all_corners_black is monkeypatched per test. Returns (ctx, notified)."""
    now = [0.0]
    notified = []
    ctx = StepContext(
        sender=sender,
        clock=lambda: now[0],
        sleeper=lambda s: now.__setitem__(0, now[0] + s),
        event_waiter=(None if load_result is None else (lambda n, t: load_result)),
        should_abort=(lambda: abort),
        record=lambda k, p: None,
    )
    ctx.connection_grabber = lambda: "frame"
    ctx.game_mode_supplier = (lambda: game_mode)
    ctx.connection_recovery_notify = notified.append
    return ctx, notified


def test_recovery_corner_black_confirms_solo(monkeypatch):
    """Press Solo, confirm the transition via all-4-corners-black (the loading
    screen) -> success, one Solo press."""
    import ed_vision.hud_sc_indicators as hud
    calls = {"n": 0}
    monkeypatch.setattr(hud, "all_corners_black",
                        lambda fr, **k: (calls.__setitem__("n", calls["n"] + 1)
                                         or calls["n"] >= 2))  # call1 menu-lit, then black
    sender = FakeSender()
    ctx, notified = _cv_ctx(sender, game_mode="Solo")
    assert step_connection_recovery(ctx) is True
    assert notified == [True]
    assert "GalaxyMapOpen" in sender.actions() and "UI_Back" in sender.actions()
    assert sender.actions().count("UI_Select") == 3        # OK + CONTINUE + one Solo


def test_recovery_retries_solo_while_grayed(monkeypatch):
    """Grayed/authenticating modes ignore the Solo press (no corner-black); the
    step RE-PRESSES until the screen finally goes black -> >=2 Solo presses."""
    import ed_vision.hud_sc_indicators as hud
    calls = {"n": 0}
    def fake(fr, **k):
        calls["n"] += 1
        return calls["n"] != 1 and calls["n"] >= 8   # menu-lit call1; black only from call 8
    monkeypatch.setattr(hud, "all_corners_black", fake)
    sender = FakeSender()
    ctx, notified = _cv_ctx(sender, game_mode="Solo")
    assert step_connection_recovery(ctx) is True
    assert notified == [True]
    assert sender.actions().count("UI_Select") >= 4        # OK + CONTINUE + >=2 Solo


def test_recovery_refuses_non_solo_mode(monkeypatch):
    """NEVER-FLY-OPEN net: the reconnect loaded into OPEN -> refuse success, do
    NOT re-plot, notify failure (the watch can re-fire)."""
    import ed_vision.hud_sc_indicators as hud
    calls = {"n": 0}
    monkeypatch.setattr(hud, "all_corners_black",
                        lambda fr, **k: (calls.__setitem__("n", calls["n"] + 1)
                                         or calls["n"] >= 2))
    sender = FakeSender()
    ctx, notified = _cv_ctx(sender, game_mode="Open")
    assert step_connection_recovery(ctx) is False
    assert notified == [False]
    assert "GalaxyMapOpen" not in sender.actions()         # never re-plots in Open


def test_recovery_solo_stuck_fails_closed(monkeypatch):
    """Modes grayed forever (auth never completes) -> the Solo-entry budget
    exhausts, the step fails closed, presses no map keys, notifies failure."""
    import ed_vision.hud_sc_indicators as hud
    monkeypatch.setattr(hud, "all_corners_black", lambda fr, **k: False)  # never black
    sender = FakeSender()
    ctx, notified = _cv_ctx(sender, game_mode="Solo")
    assert step_connection_recovery(ctx) is False
    assert notified == [False]
    assert "GalaxyMapOpen" not in sender.actions()


def test_recovery_blind_without_grabber_keeps_legacy_sequence():
    """No connection_grabber (grabber-unwired build) -> the exact legacy blind
    macro: OK, CONTINUE, Right, Right, Solo, replot -- no corner logic, no retry."""
    sender = FakeSender()
    ctx, notified = _recovery_ctx(sender, load_result=True)   # no grabber set
    assert step_connection_recovery(ctx) is True
    assert sender.actions() == [
        "UI_Select", "UI_Select", "UI_Right", "UI_Right",
        "UI_Select", "GalaxyMapOpen", "UI_Back"]


def test_recovery_navigates_to_solo_by_sight(monkeypatch):
    """With the mode-highlight detector the step drives the cursor to Solo BY
    SIGHT -- pressing UI_Right until it READS Solo (index 2), never overshooting
    and never blind-landing on Open. (operator 2026-07-13 mode-highlight detector.)"""
    import ed_vision.hud_sc_indicators as hud
    sender = FakeSender()
    # the detector reports the cursor as (#UI_Right - #UI_Left), clamped 0..4:
    def fake_idx(fr, **k):
        a = sender.actions()
        return max(0, min(4, a.count("UI_Right") - a.count("UI_Left")))
    monkeypatch.setattr(hud, "highlighted_mode_index", fake_idx)
    ccalls = {"n": 0}
    monkeypatch.setattr(hud, "all_corners_black",
                        lambda fr, **k: (ccalls.__setitem__("n", ccalls["n"] + 1)
                                         or ccalls["n"] >= 2))   # menu-lit, then black
    ctx, notified = _cv_ctx(sender, game_mode="Solo")
    assert step_connection_recovery(ctx) is True
    assert notified == [True]
    assert sender.actions().count("UI_Right") == 2     # Open->Private->Solo, no overshoot
    assert "UI_Left" not in sender.actions()


# ---- route re-plot: gate close on map-open, gate completion on a FRESH route ---
# (operator 2026-07-13: after reload the ship is in real-space with the route no
#  longer active; GalaxyMapOpen re-plots it, and it takes several seconds.)

def _replot_ctx(sender, *, waiter, status=None, nav=None):
    logs = []
    notified = []
    ctx = StepContext(
        sender=sender, sleeper=lambda s: None, clock=lambda: 0.0,
        event_waiter=waiter, should_abort=(lambda: False),
        status_supplier=(status or (lambda: None)),
        navroute_supplier=(nav or (lambda: None)),
        record=lambda k, p: logs.append((k, p)),
    )
    ctx.connection_recovery_notify = notified.append
    return ctx, logs, notified


def test_recovery_replot_gates_on_map_open_and_navroute():
    """Press GalaxyMapOpen, wait until the map is OPEN (GuiFocus 6) before
    closing, then wait for the fresh NavRoute event -> route_plotted True."""
    sender = FakeSender()
    def waiter(name, t): return name in ("LoadGame", "NavRoute")
    def status():                       # GuiFocus 6 only after the map is opened
        return SimpleNamespace(
            gui_focus=6 if "GalaxyMapOpen" in sender.actions() else 0)
    ctx, logs, notified = _replot_ctx(sender, waiter=waiter, status=status)
    assert step_connection_recovery(ctx) is True
    assert notified == [True]
    assert "GalaxyMapOpen" in sender.actions() and "UI_Back" in sender.actions()
    rep = [p for k, p in logs if k == "ConnectionRecoveryReplotted"]
    assert rep and rep[0]["route_plotted"] is True


def test_recovery_replot_detects_fresh_route_via_navroute_file():
    """NavRoute event missed, but NavRoute.json changes to a route DIFFERENT from
    the stale pre-load one -> fresh-route gate still passes (not a stale false-pass)."""
    sender = FakeSender()
    def waiter(name, t): return name == "LoadGame"   # NavRoute event never fires
    def nav():                          # stale [7] before close, fresh [42,99] after
        if "UI_Back" in sender.actions():
            return SimpleNamespace(route=[SimpleNamespace(system_address=42),
                                          SimpleNamespace(system_address=99)])
        return SimpleNamespace(route=[SimpleNamespace(system_address=7)])
    ctx, logs, notified = _replot_ctx(sender, waiter=waiter, nav=nav)
    assert step_connection_recovery(ctx) is True
    rep = [p for k, p in logs if k == "ConnectionRecoveryReplotted"]
    assert rep and rep[0]["route_plotted"] is True


def test_recovery_replot_stale_route_does_not_false_pass():
    """A STALE NavRoute.json that never changes (same addrs as pre-load) must NOT
    count as a fresh plot -> route_plotted False (resuming on stale hops is wrong)."""
    sender = FakeSender()
    def waiter(name, t): return name == "LoadGame"
    stale = lambda: SimpleNamespace(route=[SimpleNamespace(system_address=7)])
    ctx, logs, notified = _replot_ctx(sender, waiter=waiter, nav=stale)
    assert step_connection_recovery(ctx) is True     # best-effort completes
    rep = [p for k, p in logs if k == "ConnectionRecoveryReplotted"]
    assert rep and rep[0]["route_plotted"] is False   # stale never counts as fresh


def test_recovery_replot_best_effort_when_route_never_plots():
    """No NavRoute event and no fresh route -> route_plotted False but the step
    still completes (notify True) after the bounded wait (nav computer may not
    reach; proceed loudly rather than hang)."""
    sender = FakeSender()
    def waiter(name, t): return name == "LoadGame"   # LoadGame yes, NavRoute never
    ctx, logs, notified = _replot_ctx(sender, waiter=waiter)
    assert step_connection_recovery(ctx) is True
    assert notified == [True]
    rep = [p for k, p in logs if k == "ConnectionRecoveryReplotted"]
    assert rep and rep[0]["route_plotted"] is False
