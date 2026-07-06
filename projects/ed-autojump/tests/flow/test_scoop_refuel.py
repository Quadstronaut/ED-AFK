"""scoop_refuel: the arrival pit stop (spec 2026-06-06-scoop-refuel-design).

The step is a Status.json-gated state machine, so every test drives it with
a scripted status-by-time function and a fake clock advanced by the sleeper —
no real sleeps, no real game.
"""

from pathlib import Path
from types import SimpleNamespace

from ed_core.flow.context import ShipFuel, StepContext
from ed_autojump.flow.dispatcher import FlowRunner
from ed_core.flow.loader import load_procedures
from ed_autojump.flow.steps import STEP_REGISTRY, _scoop_window_rate
from ed_autojump.fsd.scoops import scoop_max_rate_t_s
from tests.flow import FakeSender

PROC_DIR = Path(__file__).resolve().parents[2] / "procedures"

_SIX_A = ShipFuel(capacity_t=16.0, scoop_max_rate_t_s=0.878)  # small tank = fast tests


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _st(fuel, scooping=False):
    return SimpleNamespace(fuel=SimpleNamespace(fuel_main=fuel),
                           scooping_fuel=scooping)


def _ctx(script, *, clock=None, sender=None, ship=_SIX_A, star="G",
         abort=None, log=None, jump_age_supplier=None):
    """script: callable(t) -> Status-like or None.

    jump_age_supplier: optional override for the stale-arrival gate. Default
    UNWIRED (the context default = `lambda: None`), so a bare _ctx() proceeds
    exactly as before the gate existed (regression guard)."""
    clock = clock or _Clock()
    sender = sender or FakeSender()
    kw = {}
    if jump_age_supplier is not None:
        kw["jump_age_supplier"] = jump_age_supplier
    return StepContext(
        sender=sender,
        clock=clock,
        sleeper=lambda s: setattr(clock, "t", clock.t + s),
        status_supplier=lambda: script(clock.t),
        arrival_star_class_supplier=lambda: star,
        ship_fuel_supplier=lambda: ship,
        should_abort=abort or (lambda: False),
        record=log,
        **kw,
    )


def _recorder():
    rows = []
    return rows, lambda kind, payload: rows.append((kind, payload))


# ---- rate math: the stale-poll trap (council must-fix) ---------------------

def test_window_rate_is_slope_over_changed_samples():
    samples = [(0.0, 10.0), (1.0, 10.5), (2.0, 11.0)]
    assert _scoop_window_rate(samples, now=2.0, window_s=2.0) == 0.5


def test_window_rate_single_recent_change_is_unknown_not_zero():
    # One changed sample inside the window = Status.json simply hasn't been
    # rewritten yet. Reading that as rate=0 was the stale-poll trap — it
    # must be None (no judgement), never 0.0 (stall evidence).
    samples = [(1.5, 10.0)]
    assert _scoop_window_rate(samples, now=2.0, window_s=2.0) is None


def test_window_rate_no_change_beyond_window_is_true_zero():
    # The newest CHANGE is older than the whole window: nothing has flowed
    # for window_s — that is a true 0.0 (stall evidence).
    samples = [(0.0, 10.0)]
    assert _scoop_window_rate(samples, now=3.0, window_s=2.0) == 0.0


def test_window_rate_empty_is_unknown():
    assert _scoop_window_rate([], now=1.0, window_s=2.0) is None


# ---- scoop table lookup ----------------------------------------------------

def test_scoop_table_6a_matches_edcd():
    assert scoop_max_rate_t_s("int_fuelscoop_size6_class5") == 0.878


def test_scoop_table_1e_smallest():
    assert scoop_max_rate_t_s("int_fuelscoop_size1_class1") == 0.018


def test_scoop_table_unknown_items_are_none():
    # Never guess a rate: a non-scoop module and an out-of-table size both
    # report None and the step skips fail-safe (g1).
    assert scoop_max_rate_t_s("int_dockingcomputer_advanced") is None
    assert scoop_max_rate_t_s("int_fuelscoop_size9_class5") is None


# ---- skip gates (no-op success, no presses) --------------------------------

def test_skip_without_status_fuel():
    rows, log = _recorder()
    sender = FakeSender()
    ctx = _ctx(lambda t: None, sender=sender, log=log)
    assert STEP_REGISTRY["scoop_refuel"](ctx) is True
    assert sender.actions() == []
    assert rows[0] == ("ScoopRefuelSkipped", {"reason": "no_status_fuel"})


def test_skip_without_ship_fuel_facts():
    rows, log = _recorder()
    sender = FakeSender()
    ctx = _ctx(lambda t: _st(5.0), sender=sender, ship=None, log=log)
    assert STEP_REGISTRY["scoop_refuel"](ctx) is True
    assert sender.actions() == []
    assert rows[0][1]["reason"] == "no_ship_fuel_facts"


def test_skip_unknown_scoop_rate():
    # Loadout seen but the scoop module wasn't recognized -> rate None -> g1.
    rows, log = _recorder()
    ctx = _ctx(lambda t: _st(5.0),
               ship=ShipFuel(capacity_t=16.0, scoop_max_rate_t_s=None),
               log=log)
    assert STEP_REGISTRY["scoop_refuel"](ctx) is True
    assert rows[0][1]["reason"] == "no_ship_fuel_facts"


def test_skip_non_scoopable_star():
    rows, log = _recorder()
    sender = FakeSender()
    ctx = _ctx(lambda t: _st(5.0), sender=sender, star="DA", log=log)
    assert STEP_REGISTRY["scoop_refuel"](ctx) is True
    assert sender.actions() == []
    assert rows[0][1]["reason"] == "not_scoopable"


def test_skip_unknown_star_class():
    rows, log = _recorder()
    ctx = _ctx(lambda t: _st(5.0), star=None, log=log)
    assert STEP_REGISTRY["scoop_refuel"](ctx) is True
    assert rows[0][1]["reason"] == "not_scoopable"


def test_skip_tank_healthy():
    # 12/16 = 0.75 >= refuel_below 0.70 -> no pit stop.
    rows, log = _recorder()
    sender = FakeSender()
    ctx = _ctx(lambda t: _st(12.0), sender=sender, log=log)
    assert STEP_REGISTRY["scoop_refuel"](ctx) is True
    assert sender.actions() == []
    assert rows[0][1]["reason"] == "tank_healthy"


# ---- Q4: always top off at the destination (refuel_below=1.0) --------------

def test_arrival_toml_scoop_trigger_and_throttle():
    """OPERATOR 2026-07-06 final: refuel_below = 0.75 — scoop when the tank is
    under three-quarters (supersedes the same-day 1.0 restore and council-A's
    0.50, which drained the tank live skipping 'tank_healthy' at 85%).
    approach_pct = 25 (operator reverted his own same-day 50% order after
    live run 231135 overshot the scoop band: "it never stood a chance").
    The destination top-off (0.99) stays owned by route_complete_park.toml."""
    procs = load_procedures(PROC_DIR)
    arrival = procs["arrival"]
    scoop = next(s for s in arrival.steps if s.action == "scoop_refuel")
    assert scoop.params.get("refuel_below") == 0.75
    assert scoop.params.get("approach_pct") == 25


def test_refuel_below_one_does_not_skip_healthy_tank_scoopable():
    """At refuel_below=1.0 a HEALTHY tank on a SCOOPABLE star no longer
    short-circuits 'tank_healthy' — the pit stop STARTS (ScoopStart logged). The
    full_epsilon already-full short-circuit and is_scoopable gate are unaffected."""
    rows, log = _recorder()
    sender = FakeSender()
    # 12/16 = 0.75: healthy under the old 0.70, but below refuel_below=1.0 ->
    # proceed. Not within full_epsilon of capacity. G is scoopable (KGBFOAM).
    ctx = _ctx(lambda t: _st(12.0), sender=sender, log=log, star="G")
    STEP_REGISTRY["scoop_refuel"](ctx, refuel_below=1.0, budget_s=0.0)
    reasons = [p.get("reason") for k, p in rows if k == "ScoopRefuelSkipped"]
    assert "tank_healthy" not in reasons          # no longer skips a healthy tank
    assert any(k == "ScoopStart" for k, _ in rows)   # the pit stop began


def test_refuel_below_one_still_skips_non_scoopable_star():
    """refuel_below=1.0 does NOT override the is_scoopable gate — a non-scoopable
    star (Y dwarf, not in KGBFOAM) still skips ('not_scoopable'), regardless of fuel."""
    rows, log = _recorder()
    sender = FakeSender()
    ctx = _ctx(lambda t: _st(2.0), sender=sender, log=log, star="Y")
    assert STEP_REGISTRY["scoop_refuel"](ctx, refuel_below=1.0) is True
    assert sender.actions() == []
    assert rows[0][1]["reason"] == "not_scoopable"


# ---- the full pit stop -----------------------------------------------------

def _happy_script(t):
    """Approach 2s -> scoop band at 0.3 t/s (below the 0.439 standoff) ->
    fast band at 0.6 t/s from t=6 -> tank (16t, eps 0.2) full at ~13.7s."""
    if t < 2.0:
        return _st(10.0, scooping=False)
    if t < 6.0:
        return _st(10.0 + 0.3 * (t - 2.0), scooping=True)
    return _st(min(16.0, 11.2 + 0.6 * (t - 6.0)), scooping=True)


def test_happy_path_approach_standoff_hold_full():
    rows, log = _recorder()
    sender = FakeSender()
    ctx = _ctx(_happy_script, sender=sender, log=log)
    assert STEP_REGISTRY["scoop_refuel"](ctx) is True

    kinds = [k for k, _ in rows]
    assert "ScoopStart" in kinds
    assert "ScoopStandoff" in kinds          # rate crossed 50% of max
    outcome = dict(rows)["ScoopRefuelOutcome"]
    assert outcome["reason"] == "full"
    assert outcome["fuel_end"] >= 15.8       # capacity - full_epsilon
    assert outcome["scooped_t"] > 5.0

    acts = sender.actions()
    # Approach throttle first; throttle CUT at standoff (before the final
    # zero in DONE).
    assert acts[0] == "SetSpeed25"
    assert "SetSpeedZero" in acts
    assert acts.index("SetSpeed25") < acts.index("SetSpeedZero")


def test_entry_already_scooping_goes_straight_to_rate_phase():
    # Event-gates-need-state-check law: restarted mid-scoop, the flag
    # already holds — never wait for a transition that won't come.
    rows, log = _recorder()
    ctx = _ctx(lambda t: _st(min(16.0, 10.0 + 0.6 * t), scooping=True),
               log=log)
    assert STEP_REGISTRY["scoop_refuel"](ctx) is True
    start = dict(rows)["ScoopStart"]
    assert start["state"] == "scoop"
    assert dict(rows)["ScoopRefuelOutcome"]["reason"] == "full"


def test_already_full_skips_without_flying():
    rows, log = _recorder()
    sender = FakeSender()
    # 11/16 = 0.69 < refuel_below, but within full_epsilon=6 of capacity.
    ctx = _ctx(lambda t: _st(11.0), sender=sender, log=log,
               ship=_SIX_A)
    ok = STEP_REGISTRY["scoop_refuel"](ctx, full_epsilon=6.0)
    assert ok is True
    assert sender.actions() == []
    assert rows[0][1]["reason"] == "already_full"


def test_budget_is_a_fail_backstop_never_success():
    # Star never grants the flag (e.g. a non-scoopable close companion was
    # the actual body ahead): the 5-min budget FAILS the step — throttle
    # zeroed, arrival continues into the climb-out (step is non-required).
    rows, log = _recorder()
    sender = FakeSender()
    clock = _Clock()
    ctx = _ctx(lambda t: _st(5.0, scooping=False), clock=clock,
               sender=sender, log=log)
    assert STEP_REGISTRY["scoop_refuel"](ctx, budget_s=30.0) is False
    outcome = dict(rows)["ScoopRefuelOutcome"]
    assert outcome["reason"] == "no_scoop"
    assert clock.t >= 30.0
    assert sender.actions()[-1] == "SetSpeedZero"   # never left throttled-in


def test_budget_fail_mid_scoop_is_slow_scoop():
    rows, log = _recorder()
    # Scooping but glacially: rate 0.05 t/s never hits standoff, tank never
    # fills inside the budget.
    ctx = _ctx(lambda t: _st(5.0 + 0.05 * t, scooping=True), log=log)
    assert STEP_REGISTRY["scoop_refuel"](ctx, budget_s=20.0) is False
    assert dict(rows)["ScoopRefuelOutcome"]["reason"] == "slow_scoop"


def test_stall_in_hold_reapproaches_once():
    rows, log = _recorder()
    sender = FakeSender()

    def script(t):
        if t < 1.0:
            return _st(10.0, scooping=False)
        if t < 4.0:                       # fast band -> standoff -> hold
            return _st(10.0 + 0.6 * (t - 1.0), scooping=True)
        if t < 9.0:                       # drifted out: flag drops, flow stops
            return _st(11.8, scooping=False)
        return _st(min(16.0, 11.8 + 0.6 * (t - 9.0)), scooping=True)

    ctx = _ctx(script, sender=sender, log=log)
    assert STEP_REGISTRY["scoop_refuel"](ctx) is True
    kinds = [k for k, _ in rows]
    assert "ScoopStall" in kinds
    assert dict(rows)["ScoopRefuelOutcome"]["reason"] == "full"
    # throttle: 25 (approach), 0 (standoff), 25 (re-approach), 0 (standoff),
    # 0 (DONE) — at minimum two approach presses.
    assert sender.actions().count("SetSpeed25") == 2


def test_abort_stops_pressing_immediately():
    # Smack-preempt and operator panic both arrive via should_abort: the
    # step must exit within one poll WITHOUT a trailing throttle tap (the
    # in-step contract: after abort, no more presses).
    rows, log = _recorder()
    sender = FakeSender()
    clock = _Clock()
    polls = {"n": 0}

    def abort():
        polls["n"] += 1
        return polls["n"] > 3

    ctx = _ctx(lambda t: _st(5.0, scooping=False), clock=clock,
               sender=sender, abort=abort, log=log)
    assert STEP_REGISTRY["scoop_refuel"](ctx) is False
    assert dict(rows)["ScoopRefuelOutcome"]["reason"] == "abort"
    assert sender.actions() == ["SetSpeed25"]       # approach tap only


def test_unbound_throttle_fails_clean():
    sender = FakeSender(unbound={"SetSpeed25"})
    ctx = _ctx(lambda t: _st(5.0))
    ctx.sender = sender
    assert STEP_REGISTRY["scoop_refuel"](ctx) is False


# ---- stale-arrival skip gate (F1, the 11:57Z incident) ---------------------

def test_stale_arrival_skips_with_zero_presses():
    # A restart 25 min after FSDJump entered the approach loop pointed nowhere.
    # age past the fresh window -> skip, no keys ever pressed against the star.
    rows, log = _recorder()
    sender = FakeSender()
    ctx = _ctx(lambda t: _st(5.0), sender=sender, log=log,
               jump_age_supplier=lambda: 700.0)
    assert STEP_REGISTRY["scoop_refuel"](ctx) is True
    assert sender.actions() == []
    skip = dict(rows)["ScoopRefuelSkipped"]
    assert skip["reason"] == "stale_arrival"
    assert skip["age_s"] == 700.0


def test_fresh_arrival_proceeds_into_the_scoop():
    # Age inside the fresh window -> the happy script runs the full pit stop.
    rows, log = _recorder()
    sender = FakeSender()
    ctx = _ctx(_happy_script, sender=sender, log=log,
               jump_age_supplier=lambda: 30.0)
    assert STEP_REGISTRY["scoop_refuel"](ctx) is True
    assert "ScoopStart" in [k for k, _ in rows]


def test_stale_arrival_boundary_proceeds_at_window():
    # Gate is `>`, not `>=`: exactly at the window the arrival still counts as
    # fresh and the scoop proceeds.
    ctx = _ctx(_happy_script, jump_age_supplier=lambda: 120.0)
    assert STEP_REGISTRY["scoop_refuel"](ctx) is True


def test_stale_arrival_boundary_skips_just_past_window():
    rows, log = _recorder()
    sender = FakeSender()
    ctx = _ctx(lambda t: _st(5.0), sender=sender, log=log,
               jump_age_supplier=lambda: 120.001)
    assert STEP_REGISTRY["scoop_refuel"](ctx) is True
    assert sender.actions() == []
    assert dict(rows)["ScoopRefuelSkipped"]["reason"] == "stale_arrival"


def test_unwired_jump_age_proceeds_exactly_as_today():
    # Regression guard: a bare _ctx() (jump_age unwired -> None) must take the
    # live path, never the stale-skip — None fails toward working behavior.
    rows, log = _recorder()
    ctx = _ctx(_happy_script, log=log)
    assert STEP_REGISTRY["scoop_refuel"](ctx) is True
    assert "ScoopStart" in [k for k, _ in rows]


def test_dispatcher_jump_age_uses_event_timestamp_not_replay_clock():
    # THE test that catches the monotonic bug: now_utc frozen 25 min after the
    # FSDJump's OWN journal timestamp -> age ~1500s (stale), NOT ~0 as
    # _event_times["jump"] (clock() at replay time) would have read.
    import datetime as _dt
    now = _dt.datetime(2026, 6, 7, 11, 57, 0, tzinfo=_dt.timezone.utc)
    r = FlowRunner(procedures={}, sender=FakeSender(), now_utc=lambda: now)
    # No FSDJump seen yet -> None.
    assert r._make_context().jump_age_supplier() is None
    r._apply_state(_ev("FSDJump", body_type="Star", star_system="Lyncis",
                       timestamp="2026-06-07T11:32:00Z"))
    age = r._make_context().jump_age_supplier()
    assert abs(age - 1500.0) < 0.01


# ---- dispatcher wiring -----------------------------------------------------

def _runner():
    return FlowRunner(procedures={}, sender=FakeSender())


def _ev(name, **kw):
    return SimpleNamespace(event=name, **kw)


def test_hyperspace_startjump_tracks_arrival_star():
    r = _runner()
    r._apply_state(_ev("StartJump", jump_type="Hyperspace", star_class="K"))
    assert r._arrival_star_class == "K"


def test_supercruise_startjump_does_not_clobber():
    # Council must-fix: SC StartJumps carry star_class=None — after any SC
    # entry the tracked arrival star must survive, or g2 skips forever.
    r = _runner()
    r._apply_state(_ev("StartJump", jump_type="Hyperspace", star_class="G"))
    r._apply_state(_ev("StartJump", jump_type="Supercruise", star_class=None))
    assert r._arrival_star_class == "G"


def test_loadout_tracks_capacity_and_scoop_rate():
    r = _runner()
    r._apply_state(_ev(
        "Loadout",
        fuel_capacity=SimpleNamespace(main=32.0),
        modules=[SimpleNamespace(item="int_dockingcomputer_advanced"),
                 SimpleNamespace(item="int_fuelscoop_size6_class5")],
    ))
    assert r._ship_fuel == ShipFuel(capacity_t=32.0, scoop_max_rate_t_s=0.878)


def test_loadout_without_scoop_has_no_rate():
    r = _runner()
    r._apply_state(_ev("Loadout",
                       fuel_capacity=SimpleNamespace(main=32.0),
                       modules=[SimpleNamespace(item="int_hyperdrive_size5_class5")]))
    assert r._ship_fuel.capacity_t == 32.0
    assert r._ship_fuel.scoop_max_rate_t_s is None


def test_context_wires_the_suppliers():
    r = _runner()
    r._apply_state(_ev("StartJump", jump_type="Hyperspace", star_class="M"))
    r._apply_state(_ev("Loadout",
                       fuel_capacity=SimpleNamespace(main=32.0),
                       modules=[SimpleNamespace(item="int_fuelscoop_size6_class5")]))
    ctx = r._make_context()
    assert ctx.arrival_star_class_supplier() == "M"
    assert ctx.ship_fuel_supplier().scoop_max_rate_t_s == 0.878


# ---- procedure wiring (nothing stays unwired) ------------------------------

def test_arrival_runs_the_pit_stop_before_the_star_lock():
    """REWIRE 2026-06-27 (flow-redesign #1): arrival is now
    [set_throttle, scoop_refuel, nav_supercruise_star]. The pit stop still runs
    BEFORE the SC-assist star lock, and the scoop stays best-effort with the
    operator's 5-minute backstop. (The old get-around get-around/climb-out steps —
    nav_panel_target/sc_assist_orbit — are gone; traversal owns the hop.)"""
    procs = load_procedures(PROC_DIR)
    actions = [s.action for s in procs["arrival"].steps]
    assert "scoop_refuel" in actions
    # pit stop BEFORE the star SC-assist (which replaced the old get-around).
    assert actions.index("scoop_refuel") < actions.index("nav_supercruise_star")
    scoop = next(s for s in procs["arrival"].steps
                 if s.action == "scoop_refuel")
    assert scoop.required is False           # best-effort by design
    assert scoop.params["budget_s"] == 300.0  # operator's 5-minute backstop
