"""A STALE SupercruiseExit drop must NOT dispatch smack_recovery.

Slegoae UB-V b6-0 -> EC-A c2-2 (live session 2026-07-12T023204). The
SupercruiseExit@Star at UB-V b6-0 (02:38:58) was a QUEUED event route: it
could not dispatch until the whole arrival->traversal chain returned, 3:49
later (02:42:47), by which time the ship had re-entered supercruise on its own
(SupercruiseEntry 02:41:17 -> _smacked cleared) and jumped a system onward.
_route_sc_exit then fired STALE, one system late, running smack_recovery at
Slegoae EC-A c2-2 INSTEAD of that system's arrival (no ArrivalBranch for it).

The guard: _route_sc_exit abstains when the ship is demonstrably no longer in
the smacked state (a SupercruiseEntry/FSDJump has cleared runner._smacked
since the drop). This is NOT a D2 abstain-to-idle: the ship already recovered.
"""

from types import SimpleNamespace

from ed_core.flow.dispatcher import FlowRunner
from ed_autojump.flow.boot_routes import _route_sc_exit

from . import FakeSender


def _runner():
    # No tail needed: _route_sc_exit reads runner state, not the hub.
    return FlowRunner(procedures={}, sender=FakeSender(), sleeper=lambda s: None)


def _star_exit():
    return SimpleNamespace(event="SupercruiseExit", body_type="Star")


def test_stale_sc_exit_dropped_when_not_smacked():
    """Superseded drop (a later SupercruiseEntry/FSDJump cleared _smacked) ->
    abstain: no smack_recovery dispatched, return None, record the drop."""
    r = _runner()
    ran = []
    r._run = lambda name: ran.append(name)
    r._smacked = False                 # ship already left the smacked state
    records = []
    r.record = lambda n, p: records.append((n, p))
    assert _route_sc_exit(r, _star_exit()) is None
    assert ran == []                   # smack_recovery NEVER dispatched
    assert ("SmackDispatchStale", {"body_type": "Star"}) in records


def test_live_sc_exit_still_recovers():
    """D2 always-recover preserved: a FRESH real-space Star drop (still
    smacked) dispatches smack_recovery exactly as before."""
    r = _runner()
    ran = []
    r._run = lambda name: ran.append(name)
    r._smacked = True                  # fresh live smack, not yet recovered
    r.record = lambda n, p: None
    assert _route_sc_exit(r, _star_exit()) == "smack_recovery"
    assert ran == ["smack_recovery"]
