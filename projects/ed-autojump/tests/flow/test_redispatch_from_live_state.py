"""redispatch_from_live_state — the never-strand domain driver (workstream A,
2026-07-07 council spec). Wired to runner._redispatch_driver at FlowRunner
construction (cli.py); invoked by run_live's _maybe_redispatch whenever a
required-fail abort queued a re-dispatch and the backoff window elapsed.

Re-runs the SAME classification a fresh boot would (NOT one-shot: does not
touch runner._startup_done), from live Status+journal state:
  1. real-space + FSD cooldown burning + undocked -> smack_recovery
  2. docked -> no-op
  3. in-supercruise -> the proximity gate (arrival vs sc_resume vs idle)
  4. real-space + _smacked latch (+ cooldown, the existing legacy AND-gate,
     UNCHANGED here) -> smack_recovery, ALWAYS (no smack_kind sub-gate)
  5. real-space + route -> startup
  6. real-space + no route -> NoRouteOnStartup LOUD legit-idle
"""

from types import SimpleNamespace as NS

from ed_autojump.flow.dispatcher import FlowRunner
from ed_core.flow.model import Procedure, Step

from ed_autojump.flow.boot_routes import redispatch_from_live_state

from . import FakeSender


def _ev(name, **fields):
    return NS(event=name, **fields)


def _procs():
    return {
        "startup": Procedure(name="startup", steps=(Step("target_ahead"),)),
        "arrival": Procedure(name="arrival", steps=(Step("target_next_route"),)),
        "sc_resume": Procedure(name="sc_resume", steps=(Step("target_ahead"),)),
        "smack_recovery": Procedure(
            name="smack_recovery", steps=(Step("set_throttle", {"pct": 50}),)),
    }


def _make_navroute_reader(route):
    nr = NS(route=route)
    return NS(poll=lambda: None, current=nr)


def _runner(sender, *, status, route=None):
    r = FlowRunner(
        procedures=_procs(), sender=sender, clock=lambda: 0.0,
        sleeper=lambda s: None, status_supplier=lambda: status,
    )
    if route is not None:
        r.navroute_reader = _make_navroute_reader(route)
    return r


def test_status_none_returns_none_no_crash():
    r = FlowRunner(procedures={}, sender=FakeSender(), clock=lambda: 0.0,
                   sleeper=lambda s: None, status_supplier=lambda: None)
    assert redispatch_from_live_state(r) is None


def test_realspace_cooldown_dispatches_smack_recovery():
    sender = FakeSender()
    st = NS(docked=False, in_supercruise=False, fsd_charging=False,
           fsd_cooldown=True, fsd_mass_locked=False, overheating=False)
    r = _runner(sender, status=st)
    assert redispatch_from_live_state(r) == "smack_recovery"
    assert sender.actions() == ["SetSpeed50"]


def test_docked_is_a_noop():
    sender = FakeSender()
    st = NS(docked=True, in_supercruise=False, fsd_charging=False,
           fsd_cooldown=False, fsd_mass_locked=False, overheating=False)
    r = _runner(sender, status=st)
    assert redispatch_from_live_state(r) is None
    assert sender.actions() == []


def test_in_supercruise_stale_loiter_runs_sc_resume():
    """Priority-4 of the legacy proximity gate: in supercruise, a confident
    non-local-star lock, stale jump_age -> the fast sc_resume path."""
    sender = FakeSender()
    dest = NS(name="Some Station", system=99, body=3)
    st = NS(docked=False, in_supercruise=True, fsd_charging=False,
           fsd_cooldown=False, fsd_mass_locked=False, overheating=False,
           destination=dest)
    r = _runner(sender, status=st, route=[NS(system_address=1, star_system="Wolf 359"),
                                          NS(system_address=2, star_system="Sol")])
    r._current_system = "Wolf 359"
    r._jump_age = lambda: 999.0            # stale -> not a fresh-arrival smack guard
    assert redispatch_from_live_state(r) == "sc_resume"
    assert sender.actions() == ["SelectTarget"]


def test_legacy_smacked_branch_always_recovers_no_kind_gate():
    """D2/C2 alignment (_classify_startup_legacy 459-477): _smacked + cooldown
    dispatches smack_recovery with NO smack_kind gate. Exercised directly
    against _classify_startup_legacy: this driver's OWN priority-1 cooldown
    check (a strict superset of legacy's `_smacked and st.fsd_cooldown`
    condition) always intercepts a live cooldown=True status first, so the
    ONLY way to prove legacy's branch itself carries no smack_kind gate is to
    call it directly -- the alignment is real even though this specific
    driver can never reach it (spec-mandated defensive consistency)."""
    from ed_autojump.flow.boot_routes import _classify_startup_legacy
    sender = FakeSender()
    st = NS(docked=False, in_supercruise=False, fsd_charging=False,
           fsd_cooldown=True, fsd_mass_locked=False, overheating=False)
    r = _runner(sender, status=st)
    r._on_tail_event(_ev("SupercruiseExit", body_type="Star"))
    assert r._smack_kind is None            # never CV-confirmed on a cold path
    assert _classify_startup_legacy(r, st) == "smack_recovery"
    assert sender.actions() == ["SetSpeed50"]


def test_realspace_with_route_runs_startup():
    sender = FakeSender()
    st = NS(docked=False, in_supercruise=False, fsd_charging=False,
           fsd_cooldown=False, fsd_mass_locked=False, overheating=False)
    r = _runner(sender, status=st, route=[NS(system_address=1, star_system="Wolf 359"),
                                          NS(system_address=2, star_system="Sol")])
    assert redispatch_from_live_state(r) == "startup"
    assert sender.actions() == ["SelectTarget"]


def test_realspace_no_route_is_loud_legit_idle(capsys):
    sender = FakeSender()
    records = []
    st = NS(docked=False, in_supercruise=False, fsd_charging=False,
           fsd_cooldown=False, fsd_mass_locked=False, overheating=False)
    r = _runner(sender, status=st, route=[])
    r.record = lambda n, p: records.append((n, p))
    assert redispatch_from_live_state(r) is None
    assert sender.actions() == []
    out = capsys.readouterr().out
    assert "[NO ROUTE]" in out
    assert any(n == "NoRouteOnStartup" for n, _ in records)


def test_driver_is_not_one_shot_startup_done_untouched():
    """This driver must be safe to call ANY number of times, unlike
    classify_startup -- it does not read or set runner._startup_done."""
    sender = FakeSender()
    st = NS(docked=False, in_supercruise=False, fsd_charging=False,
           fsd_cooldown=False, fsd_mass_locked=False, overheating=False)
    r = _runner(sender, status=st, route=[NS(system_address=1, star_system="Wolf 359"),
                                          NS(system_address=2, star_system="Sol")])
    r._startup_done = True                  # even with the one-shot flag ALREADY set
    assert redispatch_from_live_state(r) == "startup"
    assert redispatch_from_live_state(r) == "startup"    # fires again -- not one-shot
    assert sender.actions() == ["SelectTarget", "SelectTarget"]
