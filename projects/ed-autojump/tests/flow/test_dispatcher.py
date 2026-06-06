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


def _heat_runner(*, overheating, clock, sender=None, cooldown=10.0, record=None):
    """FlowRunner with a mutable status whose `overheating` we control."""
    sender = sender or FakeSender()
    st = SimpleNamespace(overheating=overheating)
    r = FlowRunner(
        procedures={},
        sender=sender,
        clock=clock,
        sleeper=lambda s: None,
        status_supplier=lambda: st,
        heat_eject_cooldown_s=cooldown,
        record=record,
    )
    return r, sender, st


def test_heat_guard_ejects_when_overheating():
    r, sender, _ = _heat_runner(overheating=True, clock=lambda: 100.0)
    r.heat_guard()
    assert sender.actions() == ["DeployHeatSink"]


def test_heat_guard_no_op_when_cool():
    r, sender, _ = _heat_runner(overheating=False, clock=lambda: 100.0)
    r.heat_guard()
    assert sender.actions() == []


def test_heat_guard_no_op_when_no_status():
    sender = FakeSender()
    r = FlowRunner(
        procedures={}, sender=sender, clock=lambda: 0.0, sleeper=lambda s: None,
        status_supplier=lambda: None,
    )
    r.heat_guard()
    assert sender.actions() == []


def test_heat_guard_debounces_within_cooldown():
    """Two heat_guard calls inside the cooldown window -> one eject only."""
    t = [100.0]
    r, sender, _ = _heat_runner(overheating=True, clock=lambda: t[0], cooldown=10.0)
    r.heat_guard()                # fires at t=100
    t[0] = 105.0                  # 5s later, still hot
    r.heat_guard()                # debounced, no fire
    assert sender.actions() == ["DeployHeatSink"]


def test_heat_guard_fires_again_after_cooldown():
    t = [100.0]
    r, sender, _ = _heat_runner(overheating=True, clock=lambda: t[0], cooldown=10.0)
    r.heat_guard()                # fires at t=100
    t[0] = 110.5                  # past 10s window
    r.heat_guard()                # fires again
    assert sender.actions() == ["DeployHeatSink", "DeployHeatSink"]


def test_heat_guard_missing_bind_records_and_debounces():
    """If DeployHeatSink is unbound, log it and debounce so we don't loop."""
    logs: list[tuple[str, dict]] = []
    sender = FakeSender(unbound={"DeployHeatSink"})
    r = FlowRunner(
        procedures={}, sender=sender, clock=lambda: 100.0, sleeper=lambda s: None,
        status_supplier=lambda: SimpleNamespace(overheating=True),
        record=lambda name, payload: logs.append((name, payload)),
    )
    r.heat_guard()
    r.heat_guard()                # still inside cooldown -> no retry
    assert sender.actions() == [] # nothing pressed
    assert any(n == "HeatEjectBindMissing" for n, _ in logs)


def test_make_context_threads_widget_ring_fields():
    """A FlowRunner built with the widget-ring params produces a context that
    carries all three through to the steps (the only place a real run wires
    them). Without this, widget_ring_alignment=on is inert at runtime."""
    sender = FakeSender()
    reader = object()
    grab = lambda: object()
    r = FlowRunner(
        procedures={}, sender=sender, clock=lambda: 0.0, sleeper=lambda s: None,
        status_supplier=lambda: None,
        widget_ring_enabled=True, widget_ring_reader=reader,
        widget_frame_grabber=grab,
    )
    ctx = r._make_context()
    assert ctx.widget_ring_enabled is True
    assert ctx.widget_ring_reader is reader
    assert ctx.widget_frame_grabber is grab


class _FakeOverlay:
    def __init__(self):
        self.events = []
        self.steps = []

    def event(self, text):
        self.events.append(text)

    def step(self, proc, action, idx, total):
        self.steps.append((proc, action, idx, total))


def test_overlay_threads_into_context_and_jump_event():
    sender = FakeSender()
    ov = _FakeOverlay()
    procs = {"arrival": Procedure(name="arrival", steps=(Step("target_next_route"),))}
    r = FlowRunner(
        procedures=procs, sender=sender, clock=lambda: 0.0, sleeper=lambda s: None,
        status_supplier=lambda: SimpleNamespace(
            docked=False, in_supercruise=True, fsd_charging=False,
            fsd_cooldown=False, fsd_mass_locked=False, overheating=False),
        overlay=ov,
    )
    assert r._make_context().overlay is ov                 # threaded through
    r.dispatch(_ev("FSDJump", body_type="Star", star_system="Sol"))
    assert ov.events == ["Jump 1: Sol"]                    # counter + system
    assert ("arrival", "target_next_route", 1, 1) in ov.steps  # per-step status


def test_make_context_widget_ring_defaults_off():
    """Default construction leaves the fine pass disabled and unwired."""
    r = FlowRunner(procedures={}, sender=FakeSender(), clock=lambda: 0.0,
                   sleeper=lambda s: None, status_supplier=lambda: None)
    ctx = r._make_context()
    assert ctx.widget_ring_enabled is False
    assert ctx.widget_ring_reader is None
    assert ctx.widget_frame_grabber is None


def test_heat_tick_pauses_while_input_exclusive():
    """Spec 2026-06-06: a UI macro owning input suppresses the heatsink tap;
    release resumes it on the next tick."""
    r, sender, _ = _heat_runner(overheating=True, clock=lambda: 100.0)
    with r._exclusive_input():
        r._heat_tick()
    assert sender.actions() == []          # paused — nothing pressed
    r._heat_tick()                          # guard released
    assert sender.actions() == ["DeployHeatSink"]


def test_exclusive_guard_is_a_counter_not_a_bool():
    """Nested/parallel holders: releasing one must not clear the other."""
    r, _, _ = _heat_runner(overheating=False, clock=lambda: 0.0)
    with r._exclusive_input():
        with r._exclusive_input():
            assert r.input_exclusive() is True
        assert r.input_exclusive() is True   # outer holder still active
    assert r.input_exclusive() is False


def test_interpreter_wraps_exclusive_steps_in_guard():
    """sc_assist_orbit / nav_panel_target run inside ctx.exclusive_guard —
    held during the step, released after, even though the step presses keys."""
    from contextlib import contextmanager
    from ed_autojump.flow.interpreter import run_procedure
    from ed_autojump.flow.context import StepContext

    held_during: list[bool] = []
    state = {"held": False}

    @contextmanager
    def guard():
        state["held"] = True
        try:
            yield
        finally:
            state["held"] = False

    def spy_step(ctx, **params):
        held_during.append(state["held"])
        return True

    proc = Procedure(name="p", steps=(
        Step("sc_assist_orbit"), Step("target_ahead")))
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      exclusive_guard=guard)
    registry = {"sc_assist_orbit": spy_step, "target_ahead": spy_step}
    run_procedure(proc, ctx, registry=registry)
    assert held_during == [True, False]     # macro held it, tap did not
    assert state["held"] is False           # released at the end


def test_heat_watchdog_loop_exits_on_stop_and_panic():
    import threading
    r, sender, _ = _heat_runner(overheating=False, clock=lambda: 0.0)
    stop = threading.Event()
    stop.set()
    r._heat_watchdog_loop(stop)             # returns immediately, no hang
    r2, _, _ = _heat_runner(overheating=False, clock=lambda: 0.0)
    r2.stop_requested = True
    r2._heat_watchdog_loop(threading.Event())  # _should_abort path
    assert sender.actions() == []


class _FakeTail:
    """Yields scripted event batches, one batch per .step() call."""
    def __init__(self, batches):
        self._batches = list(batches)
    def step(self):
        return self._batches.pop(0) if self._batches else []


def test_tail_hub_every_subscriber_sees_every_event():
    """REGRESSION GUARD for the honk/main waiter race: two concurrent
    waiters used to split tail events at random (each event consumed by
    exactly one). The hub broadcasts — both subscribers see the event."""
    from ed_autojump.flow.dispatcher import _TailHub
    tail = _FakeTail([[_ev("StartJump")]])
    hub = _TailHub(tail)
    a, b = hub.subscribe(), hub.subscribe()
    got_a = hub.poll(a)                      # this poll pumps the tail
    got_b = hub.poll(b)                      # b still gets the same event
    assert [e.event for e in got_a] == ["StartJump"]
    assert [e.event for e in got_b] == ["StartJump"]


def test_tail_hub_unsubscribed_handle_polls_empty():
    """A track that outlives its join window polls into silence, not a
    KeyError — its own key-release backstop ends it."""
    from ed_autojump.flow.dispatcher import _TailHub
    hub = _TailHub(_FakeTail([[_ev("StartJump")]]))
    h = hub.subscribe()
    hub.unsubscribe(h)
    assert hub.poll(h) == []


def test_waiter_no_longer_swallows_dispatchable_events():
    """An FSDJump pumped while a step's waiter is polling for StartJump must
    still reach run_live's queue and dispatch the arrival flow afterwards."""
    from ed_autojump.flow.dispatcher import _TailHub
    tail = _FakeTail([[_ev("FSDJump", body_type="Star")]])
    hub = _TailHub(tail)
    main = hub.subscribe()
    waiter = hub.subscribe()
    # The waiter pumps the tail looking for StartJump and doesn't find it...
    assert not any(e.event == "StartJump" for e in hub.poll(waiter))
    # ...but the FSDJump is still waiting in the main queue.
    assert [e.event for e in hub.poll(main)] == ["FSDJump"]


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
