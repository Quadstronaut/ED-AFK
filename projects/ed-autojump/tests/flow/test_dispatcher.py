from types import SimpleNamespace

from ed_autojump.flow.dispatcher import FlowRunner
from ed_autojump.flow.model import Procedure, Step
from tests.flow import FakeSender


def _ev(name, **fields):
    return SimpleNamespace(event=name, **fields)


def _runner(procs, sender, clock):
    return FlowRunner(
        procedures=procs,
        sender=sender,
        clock=clock,
        sleeper=lambda s: None,
        status_supplier=lambda: SimpleNamespace(
            docked=False, in_supercruise=True, fsd_charging=False,
            fsd_cooldown=False, fsd_mass_locked=False, overheating=False),
    )


def test_fsdjump_runs_arrival():
    sender = FakeSender()
    procs = {"arrival": Procedure(name="arrival", steps=(Step("target_next_route"),))}
    r = _runner(procs, sender, clock=lambda: 0.0)
    r.dispatch(_ev("FSDJump", body_type="Star"))
    assert sender.actions() == ["TargetNextRouteSystem"]


def test_supercruise_exit_at_star_runs_smack_and_records_drop_time():
    sender = FakeSender()
    procs = {"smack_recovery": Procedure(name="smack_recovery", steps=(Step("target_ahead"),))}
    t = [500.0]
    r = _runner(procs, sender, clock=lambda: t[0])
    r.dispatch(_ev("SupercruiseExit", body_type="Star"))
    assert sender.actions() == ["SelectTarget"]
    assert r.event_time("drop") == 500.0


def test_supercruise_exit_not_star_is_ignored():
    sender = FakeSender()
    procs = {"smack_recovery": Procedure(name="smack_recovery", steps=(Step("target_ahead"),))}
    r = _runner(procs, sender, clock=lambda: 0.0)
    r.dispatch(_ev("SupercruiseExit", body_type="Planet"))
    assert sender.actions() == []


def test_parallel_track_runs_alongside_main():
    sender = FakeSender()
    procs = {
        "arrival": Procedure(name="arrival", steps=(Step("target_next_route"),),
                             parallel_tracks=("honk",)),
        "honk": Procedure(name="honk", parallel=True,
                          steps=(Step("press", {"bind": "ExplorationFSSDiscoveryScan", "hold_s": 0.01}),)),
    }
    r = _runner(procs, sender, clock=lambda: 0.0)
    r.dispatch(_ev("FSDJump", body_type="Star"))
    acts = sender.actions()
    assert "ExplorationFSSDiscoveryScan" in acts   # honk fired
    assert "TargetNextRouteSystem" in acts          # arrival fired
