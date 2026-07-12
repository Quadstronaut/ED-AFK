"""Mid-step journal delivery via the should_abort hub pump (operator "ship
them", 2026-07-11).

Live 23:43:43 (session 234324): SupercruiseExit@Star fired in the journal
while sc_resume ran a sleeper-only step (orient_compass). Nothing pumped the
tail hub — events were only consumed inside wait-steps' event_waiter or by a
live honk track — so the D2 always-recover route never saw the event and the
scene kept flying the smacked ship. _run_abort now pumps the hub first: the
event routes through _on_tail_event -> _record_event_time -> _preempt, and
the SAME poll returns True.
"""

from types import SimpleNamespace

from ed_core.flow.dispatcher import FlowRunner

from . import FakeSender


class _FakeTail:
    """JournalTail stand-in: step() yields the scripted events once."""

    def __init__(self, events):
        self._events = list(events)

    def step(self):
        evs, self._events = self._events, []
        return evs


def _star_drop():
    return SimpleNamespace(event="SupercruiseExit", body_type="Star")


def _runner(events):
    return FlowRunner(procedures={}, sender=FakeSender(),
                      sleeper=lambda s: None, tail=_FakeTail(events))


def test_star_drop_lands_at_the_next_abort_poll_mid_procedure():
    """The live gap, pinned: a Star drop arriving while a preempt-eligible
    scene runs must flip should_abort() TRUE on the very next poll — no
    event_waiter, no honk track needed."""
    r = _runner([_star_drop()])
    r._running_proc = "sc_resume"
    records = []
    r.record = lambda n, p: records.append((n, p))
    assert r._run_abort() is True
    assert r._preempt == "star_smack"
    assert ("PreemptRequested",
            {"procedure": "sc_resume", "reason": "star_smack"}) in records


def test_station_drop_does_not_preempt():
    r = _runner([SimpleNamespace(event="SupercruiseExit", body_type="Station")])
    r._running_proc = "sc_resume"
    assert r._run_abort() is False
    assert r._preempt is None


def test_no_tail_pump_is_a_noop():
    r = FlowRunner(procedures={}, sender=FakeSender(), sleeper=lambda s: None)
    assert r._run_abort() is False


def test_pump_broadcasts_to_run_live_queues_too():
    """Pumping mid-step must not EAT events: run_live's subscriber still sees
    the drop afterwards (its _route_sc_exit dispatch fires post-scene)."""
    r = _runner([_star_drop()])
    r._running_proc = "sc_resume"
    handle = r._hub.subscribe()
    assert r._run_abort() is True
    pending = r._hub.poll(handle)
    assert [getattr(e, "event", None) for e in pending] == ["SupercruiseExit"]


def _fsd_jump(system="Slegoae EC-A c2-2"):
    return SimpleNamespace(event="FSDJump", star_system=system,
                           timestamp="2026-07-12T03:19:52Z")


def test_fsdjump_preempts_a_running_procedure():
    """Phroea IB-N d7-8 ram, pinned: a live FSDJump arriving mid-procedure
    means the ship is in a NEW system -- the running scene (traversal's jump
    retry) is now obsolete and blind. should_abort() must flip TRUE with
    _preempt='new_system' so run_live can flip to the new system's arrival
    before the stale scene re-issues SetSpeed100 into the just-arrived star."""
    r = _runner([_fsd_jump()])
    r._running_proc = "traversal"
    records = []
    r.record = lambda n, p: records.append((n, p))
    assert r._run_abort() is True
    assert r._preempt == "new_system"
    assert ("PreemptRequested",
            {"procedure": "traversal", "reason": "new_system"}) in records


def test_fsdjump_between_procedures_does_not_preempt():
    """Backlog replay / between-scene FSDJump (no procedure running) must NOT
    set a preempt -- the normal _route_fsd_jump -> arrival dispatch owns it,
    and a backlog jump must never abort a scene that has not started."""
    r = _runner([_fsd_jump()])
    # _running_proc left at its None default (no live scene running)
    assert r._run_abort() is False
    assert r._preempt is None
