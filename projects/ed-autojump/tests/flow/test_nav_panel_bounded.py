"""LOCK-SPEED conditional get-around (council-ratified 2026-06-07 redesign).

The bug: arrival ran the get-around (nav_panel_target + sc_assist_orbit)
UNCONDITIONALLY. nav_panel_target hunts the local STAR by walking nav-panel
rows; the panel sorts by CURRENT distance, so in a populated/drifted system the
star is buried and the unbounded walk GRINDS ~10 rows x retries for minutes,
then aborts. The get-around only matters when CLOSE to the star.

The fix uses LOCK-SPEED as the distance signal: a CLOSE star sits at/near row 0
(found inside a tight scan bound), a FAR star is buried (not found in the
bound). So:
  - nav_panel_target's scan is BOUNDED tight (max_rows=3 in arrival).
  - FOUND fast (close)      -> True  -> sc_assist_orbit runs (the get-around).
  - NOT found (far, buried) -> False -> NOT required + skip_to vaults the
                                        get-around to target_next_route.

These tests pin: the bounded scan returns False FAST (no 10-row grind) on a
buried star, a close star at row 0 is still found, the end-to-end arrival runs
the orbit when close and skips it when far, and — the closed hole — a near
star (row 0) ALWAYS runs the orbit with NO time dependence whatsoever (the
rejected jump-age design would skip a stationary-near-star restart)."""

from pathlib import Path
from types import SimpleNamespace

from ed_autojump.flow.context import StepContext
from ed_autojump.flow.interpreter import run_procedure
from ed_autojump.flow.loader import load_procedures
from ed_autojump.flow.steps import STEP_REGISTRY
from ed_autojump.vision.compass import CompassRead
from tests.flow import FakeSender

PROC_DIR = Path(__file__).resolve().parents[2] / "procedures"


def _status(dest_name, *, in_supercruise=True):
    dest = None if dest_name is None else SimpleNamespace(name=dest_name)
    return SimpleNamespace(destination=dest, in_supercruise=in_supercruise,
                           gui_focus=0)


class _DotReader:
    """Compass dot always present — a beacon renders one too (10:30Z)."""
    def read(self, frame):
        return CompassRead(found=True, offset_x=0.0, offset_y=0.0,
                           in_front=True, confidence=1.0)


def _nav_ctx(status_fn, system="Acihaut", logs=None):
    sender = FakeSender()
    ctx = StepContext(
        sender=sender, sleeper=lambda s: None,
        compass_reader=_DotReader(), frame_grabber=lambda: object(),
        compass_samples=1,
        status_supplier=status_fn,
        current_system_supplier=lambda: system,
        record=(lambda kind, payload: logs.append((kind, payload)))
        if logs is not None else None,
    )
    return ctx, sender


def _pin_count(sender):
    """One held UI_Up pin fires per macro run -> # of macro runs so far."""
    return len([h for a, h in sender.holds if a == "UI_Up" and h >= 1.0])


# ---- bounded scan: CLOSE star (row 0) found fast -----------------------------

def test_close_star_at_row_zero_found_under_tight_bound():
    """Star is the primary on row 0 -> found on the first macro run even with a
    TIGHT max_rows=3. No row walk needed."""
    ctx, sender = _nav_ctx(lambda: _status("Acihaut"))
    ok = STEP_REGISTRY["nav_panel_target"](ctx, settle_s=0.0, max_rows=3,
                                           pin_hold_s=4.0)
    assert ok is True
    # found at row 0: exactly one pin tap-down, no extra row walk
    assert sender.events.count("UI_Down") == _pin_count(sender)


def test_close_star_with_beacon_ahead_still_found_within_bound():
    """A close star with a NAV BEACON on row 0 (the 10:30Z scene): the star is
    on row 1, still inside max_rows=3 -> found. The tight bound keeps the slack
    for a beacon/station ahead of a genuinely-close star."""
    sender_holder = []

    def status():
        # wrong body on run 1 (row 0 = beacon); correct star from run 2 (row 1)
        return _status("Acihaut" if _pin_count(sender_holder[0]) >= 2
                       else "$MULTIPLAYER_SCENARIO42_TITLE;")

    ctx, sender = _nav_ctx(status)
    sender_holder.append(sender)
    ok = STEP_REGISTRY["nav_panel_target"](ctx, settle_s=0.0, max_rows=3,
                                           pin_hold_s=4.0)
    assert ok is True


# ---- bounded scan: FAR star (buried) returns False FAST ----------------------

def test_far_star_beyond_bound_returns_false_without_walking_ten_rows():
    """The star is buried past the tight bound (every probed row is a wrong
    body). The bounded scan must give up FAST (False) — NOT grind the full
    10-row default. Assert it walked at most max_rows rows."""
    logs = []
    # every row a wrong body -> the walk exhausts the bound and gives up
    ctx, sender = _nav_ctx(lambda: _status("$MULTIPLAYER_SCENARIO42_TITLE;"),
                           logs=logs)
    ok = STEP_REGISTRY["nav_panel_target"](ctx, settle_s=0.0, max_rows=3,
                                           pin_hold_s=4.0)
    assert ok is False                       # fail closed, not a wrong lock
    unverified = dict(logs)["NavPanelTargetUnverified"]
    # the row walk stopped at the BOUND, never reached the 10-row default
    assert unverified["row"] <= 3
    assert unverified["max_rows"] == 3
    # macro runs are bounded too (max_rows + max_toggles slack), nowhere near
    # the 14 the default (10+4) would allow
    assert unverified["toggles"] <= 3 + 4
    # the row walk itself never advanced 10 times (the grind it replaces)
    row_walk = sender.events.count("UI_Down") - _pin_count(sender)
    assert row_walk < 10
    assert row_walk <= 3


def test_tight_bound_walks_fewer_rows_than_default():
    """Same buried-star scene, bound=3 vs default(10): the tight bound issues
    strictly fewer row-walk presses — proving the bound is what stops the
    grind, not luck."""
    def run(max_rows):
        ctx, sender = _nav_ctx(lambda: _status("$MULTIPLAYER_SCENARIO42_TITLE;"))
        STEP_REGISTRY["nav_panel_target"](ctx, settle_s=0.0, max_rows=max_rows,
                                          pin_hold_s=4.0)
        return sender.events.count("UI_Down") - _pin_count(sender)

    assert run(3) < run(10)


# ---- end-to-end through the REAL arrival.toml ---------------------------------

def _fake_registry_running(real_lock, fired, status_fn):
    """Registry that runs the REAL nav_panel_target (so the bound + skip are
    exercised end-to-end) but fakes the rest as record-and-pass."""
    def make(name):
        def fn(ctx, **params):
            fired.append(name)
            return True
        return fn

    actions = {"set_throttle", "reset_power_distribution", "scoop_refuel",
               "sc_assist_orbit", "wait",
               "target_next_route", "orient_compass", "orient_widget_ring",
               "engage_jump", "hold_alignment", "honk"}
    reg = {a: make(a) for a in actions}

    def nav(ctx, **params):
        fired.append("nav_panel_target")
        return real_lock(ctx, **params)
    reg["nav_panel_target"] = nav
    return reg


def _arrival_ctx(status_fn, logs):
    sender = FakeSender()
    return StepContext(
        sender=sender, sleeper=lambda s: None,
        compass_reader=_DotReader(), frame_grabber=lambda: object(),
        compass_samples=1,
        status_supplier=status_fn,
        current_system_supplier=lambda: "Acihaut",
        record=lambda kind, payload: logs.append((kind, payload)),
    )


def test_arrival_close_star_runs_lock_then_orbit():
    """CLOSE arrival (star at row 0, found fast): the real nav_panel_target
    succeeds -> sc_assist_orbit RUNS (the get-around), then the lane proceeds to
    target_next_route -> orient -> jump."""
    proc = load_procedures(PROC_DIR)["arrival"]
    fired, logs = [], []
    ctx = _arrival_ctx(lambda: _status("Acihaut"), logs)
    real = STEP_REGISTRY["nav_panel_target"]
    result = run_procedure(
        proc, ctx, registry=_fake_registry_running(real, fired, None))
    assert result.completed is True and result.aborted is False
    assert "nav_panel_target" in fired
    assert "sc_assist_orbit" in fired          # the get-around RAN (close)
    assert "target_next_route" in fired
    assert "StepSkipped" not in dict(logs)     # no skip when close


def test_arrival_far_star_skips_getaround_resumes_at_target_next_route():
    """FAR arrival (star buried past the bound -> nav_panel_target returns
    False fast): the get-around (sc_assist_orbit + its wait) is VAULTED via
    skip_to, the lane resumes at target_next_route -> orient -> jump."""
    proc = load_procedures(PROC_DIR)["arrival"]
    fired, logs = [], []
    # every row a wrong body -> bounded scan gives up -> False
    ctx = _arrival_ctx(lambda: _status("$MULTIPLAYER_SCENARIO42_TITLE;"), logs)
    real = STEP_REGISTRY["nav_panel_target"]
    result = run_procedure(
        proc, ctx, registry=_fake_registry_running(real, fired, None))
    assert result.completed is True and result.aborted is False
    assert "nav_panel_target" in fired
    assert "sc_assist_orbit" not in fired      # the get-around was SKIPPED (far)
    assert "target_next_route" in fired        # resumed past the block
    skip = dict(logs)["StepSkipped"]
    assert skip["from"] == "nav_panel_target"
    assert skip["to"] == "target_next_route"


# ---- THE CLOSED HOLE: near star ALWAYS orbits, no time dependence -------------

def test_near_star_always_orbits_regardless_of_jump_age():
    """The rejected jump-age design's hole: a ship STATIONARY NEAR the star for
    a long time then restarting would (by age) skip the get-around and jump
    INTO the star. The lock-speed design closes it: 'near' is decided by LOCK
    SPEED, not time. With the star at row 0 (near), the orbit runs no matter
    what jump_age reads — stale, fresh, or unknown. We run arrival three times
    with wildly different ages; the orbit fires every time."""
    proc = load_procedures(PROC_DIR)["arrival"]
    real = STEP_REGISTRY["nav_panel_target"]
    for age in (None, 0.0, 9999.0):            # unknown, fresh, very stale
        fired, logs = [], []
        ctx = _arrival_ctx(lambda: _status("Acihaut"), logs)
        ctx.jump_age_supplier = lambda a=age: a   # the rejected design's signal
        result = run_procedure(
            proc, ctx, registry=_fake_registry_running(real, fired, None))
        assert result.completed is True, f"age={age}"
        # near star -> get-around ALWAYS runs, independent of jump age
        assert "sc_assist_orbit" in fired, f"orbit skipped at age={age}"
        assert "StepSkipped" not in dict(logs), f"skipped at age={age}"


def test_arrival_has_no_jump_age_guard_step():
    """The rejected design added a time-based guard step
    (orbit_if_fresh_arrival). The lock-speed redesign removes it entirely — the
    gate is the bounded lock itself, with zero time dependence."""
    proc = load_procedures(PROC_DIR)["arrival"]
    actions = {s.action for s in proc.steps}
    assert "orbit_if_fresh_arrival" not in actions
    assert "orbit_if_fresh_arrival" not in STEP_REGISTRY


# ---- route_complete_park: still locks + orbits the primary star --------------

def test_route_complete_park_locks_and_orbits_close_star():
    """A fresh route-end arrival is CLOSE (star at row 0). route_complete_park
    keeps the wide/required nav_panel_target -> the real bounded scan finds the
    star -> sc_assist_orbit runs. No regression from the arrival-only bound."""
    proc = load_procedures(PROC_DIR)["route_complete_park"]
    fired, logs = [], []
    ctx = _arrival_ctx(lambda: _status("Acihaut"), logs)
    real = STEP_REGISTRY["nav_panel_target"]
    result = run_procedure(
        proc, ctx, registry=_fake_registry_running(real, fired, None))
    assert result.completed is True and result.aborted is False
    assert "nav_panel_target" in fired
    assert "sc_assist_orbit" in fired
    # park has no jump half / no skip
    assert "target_next_route" not in fired
    assert "StepSkipped" not in dict(logs)


def test_route_complete_park_nav_target_is_required_no_skip():
    """The park's lock stays required with NO skip_to / NO tight bound — at a
    close route-end a not-found is a real problem to RETRY, not a far-skip."""
    proc = load_procedures(PROC_DIR)["route_complete_park"]
    nav = next(s for s in proc.steps if s.action == "nav_panel_target")
    assert nav.required is True
    assert nav.skip_to is None
    assert "max_rows" not in nav.params        # default (wide) bound
