"""NEVER-STRAND re-dispatch (workstream A, 2026-07-07 council-v2 spec).

A required-step exhaustion that is NEITHER an operator-abort NOR a preempt
must re-dispatch a recovery procedure from LIVE state, LOUD, under monotonic
bounded backoff, WITHOUT idling and WITHOUT a tight input/CPU spin.
run_live drives the actual re-dispatch call on its own loop iteration (never
nested recursion inside _run); _maybe_redispatch owns the attempt counter and
backoff deadline. These tests exercise the mechanism via ed-core's FlowRunner
directly (through the ed_autojump shim) with an INJECTED FAKE DRIVER — the
degrade-to-LOUD-idle-when-unwired path is ed-core-pure and needs no domain
module at all (spec: "still testable in ed-core via an injected fake driver").

Pure-Python: a scripted fake tail + a clock/sleeper pair where sleeper ADVANCES
the same clock the backoff math reads, so backoff progression is exercised
deterministically without any real wall-clock wait.
"""

from types import SimpleNamespace

import pytest

from ed_autojump.flow.dispatcher import FlowRunner
from ed_core.flow.model import Procedure, Step

from . import FakeSender


class _EmptyTail:
    """A journal tail that never yields an event — the 'ship sits stranded,
    nothing new happens' scene run_live's idle branch must survive without
    input/CPU spin."""

    def step(self):
        return []


class _ScriptedTail:
    """Yields nothing until `fire_at_call` polls have happened, then yields
    ONE event exactly once (simulating the ship's state changing mid-strand:
    a new journal event arrives, e.g. the operator manually recovered)."""

    def __init__(self, event, fire_at_call: int):
        self._event = event
        self._fire_at = fire_at_call
        self._calls = 0
        self._fired = False

    def step(self):
        self._calls += 1
        if not self._fired and self._calls >= self._fire_at:
            self._fired = True
            return [self._event]
        return []


def _clock_pair():
    """A clock()/sleeper(s) pair sharing mutable state: sleeper ADVANCES the
    clock by exactly the slept duration, so run_live's poll_interval_s sleeps
    and the backoff deadlines both progress the SAME simulated timeline
    without any real wall-clock wait."""
    box = {"t": 0.0}

    def clock():
        return box["t"]

    def sleeper(s):
        box["t"] += s

    return clock, sleeper, box


def _runner(*, tail, clock, sleeper, driver=None, sender=None,
           record_sink=None, overlay=None):
    r = FlowRunner(
        procedures={}, sender=sender or FakeSender(),
        clock=clock, sleeper=sleeper,
        status_supplier=lambda: None,
        tail=tail,
        redispatch_driver=driver,
        record=record_sink,
        overlay=overlay,
    )
    # run_classifiers(self) is invoked every idle loop iteration; the ONE-SHOT
    # guard makes it an immediate no-op regardless of whatever ed_autojump's
    # classify_startup rule is (or isn't) registered globally in this pytest
    # session -- these tests are ed-core-pure and must not depend on that.
    r._startup_done = True
    # The heat watchdog runs on its OWN daemon thread and calls self.sleeper()
    # concurrently with the main loop. A fake instant sleeper (no real
    # wall-clock wait) races the daemon thread's un-throttled loop against the
    # shared clock/box these tests assert on -- neutralize it here (an
    # INSTANCE attribute shadows the class method run_live looks up), no
    # production code touched.
    r._heat_watchdog_loop = lambda stop, tick_s=1.0: None
    return r


def test_persistent_strand_backoff_grows_and_caps():
    """A driver that NEVER resolves the strand (keeps re-queuing) fires at a
    monotonically-growing, capped backoff. `backoff` is the gap UNTIL the
    NEXT attempt (base*2**(attempts-1), capped): attempts land at t=0, 2, 6,
    14, 30, 60 -- gaps 2, 4, 8, 16, 30. No sender.press happens between
    attempts (no tight input loop)."""
    clock, sleeper, box = _clock_pair()
    driver_calls = []

    def driver(runner):
        driver_calls.append(runner.clock())
        runner._needs_redispatch = True     # still stranded -- queue again

    sender = FakeSender()
    r = _runner(clock=clock, sleeper=sleeper, tail=_EmptyTail(), driver=driver, sender=sender)
    r._needs_redispatch = True              # a prior _run() already queued one
    r.run_live(duration_s=90.0, poll_interval_s=1.0)
    assert len(driver_calls) >= 5
    gaps = [b - a for a, b in zip(driver_calls, driver_calls[1:])]
    assert gaps[0] == pytest.approx(2.0, abs=0.01)
    assert gaps[1] == pytest.approx(4.0, abs=0.01)
    assert gaps[2] == pytest.approx(8.0, abs=0.01)
    assert gaps[3] == pytest.approx(16.0, abs=0.01)
    assert max(gaps) <= 30.01                # never exceeds the cap
    assert gaps[-1] == pytest.approx(30.0, abs=0.01)   # capped by the 5th gap
    assert r._redispatch_attempts == len(driver_calls)
    # NEVER-STRAND forbids a tight input/CPU spin: the driver here presses
    # nothing, and NEITHER does run_live's idle machinery between attempts.
    assert sender.actions() == []


def test_first_attempt_fires_promptly_not_delayed():
    """A FRESH queue (never redispatched before) fires on the very FIRST idle
    check -- no added delay beyond what's already elapsed (no-idling law):
    `_redispatch_next_t` starts at 0.0, which is <= any real clock() reading."""
    clock, sleeper, box = _clock_pair()
    calls = []

    def driver(runner):
        calls.append(runner.clock())
        # resolves cleanly this time -- do not re-queue.

    r = _runner(clock=clock, sleeper=sleeper, tail=_EmptyTail(), driver=driver)
    r._needs_redispatch = True
    r.run_live(duration_s=5.0, poll_interval_s=1.0)
    assert calls == [0.0]                   # fired on the very first idle check
    assert r._needs_redispatch is False      # resolved -- not re-queued
    assert r._redispatch_attempts == 1


def test_unwired_driver_degrades_to_loud_bounded_idle(capsys):
    """No domain driver wired (unit tests / no ed_autojump.activate()) ->
    the backoff/attempt mechanics still run (testable in ed-core ALONE), but
    nothing crashes and no procedure dispatches. LOUD: an unwired strand-guard
    must never be silent."""
    clock, sleeper, box = _clock_pair()
    records = []
    r = _runner(clock=clock, sleeper=sleeper, tail=_EmptyTail(), driver=None,
                record_sink=lambda n, p: records.append((n, p)))
    r._needs_redispatch = True
    r.run_live(duration_s=5.0, poll_interval_s=1.0)
    out = capsys.readouterr().out
    assert "[STRAND-GUARD]" in out
    assert any(n == "RedispatchDriverUnwired" for n, _ in records)
    assert r._redispatch_attempts == 1


def test_new_journal_event_resets_backoff_attempts():
    """A NEW journal event arriving mid-strand resets the attempt counter —
    normal event routing resumes and a LATER, separate incident starts its
    own backoff from the base delay, not an inherited climbed one. Proof: the
    driver (which keeps re-queuing -- the strand never actually resolves)
    sees its attempt counter climb 1, 2, ... then DROP BACK to 1 after the
    event fires, rather than continuing to climb monotonically forever."""
    clock, sleeper, box = _clock_pair()
    seen_attempts = []

    def driver(runner):
        seen_attempts.append(runner._redispatch_attempts)
        runner._needs_redispatch = True     # keep re-queuing (persistent strand)

    ev = SimpleNamespace(event="SupercruiseEntry")
    tail = _ScriptedTail(ev, fire_at_call=4)   # let a couple idle polls happen first
    r = _runner(clock=clock, sleeper=sleeper, tail=tail, driver=driver)
    r._needs_redispatch = True
    r.run_live(duration_s=20.0, poll_interval_s=1.0)
    assert seen_attempts[0] == 1             # first-ever attempt
    assert seen_attempts.count(1) >= 2, (
        f"attempt counter never reset after the new event: {seen_attempts}")


def test_no_tight_loop_between_attempts_only_poll_and_sleep():
    """Between two redispatch attempts, run_live's idle branch is a plain
    poll+sleep — no sender.press, no busy CPU spin (only the driver, if
    invoked, may press; a driver that presses nothing leaves the sender
    silent across MANY idle iterations)."""
    clock, sleeper, box = _clock_pair()
    sender = FakeSender()
    poll_count = {"n": 0}

    class _CountingTail:
        def step(self):
            poll_count["n"] += 1
            return []

    r = _runner(clock=clock, sleeper=sleeper, tail=_CountingTail(), driver=None, sender=sender)
    r._needs_redispatch = False             # nothing queued at all
    r.run_live(duration_s=10.0, poll_interval_s=1.0)
    assert poll_count["n"] >= 9             # the loop actually ran/polled
    assert sender.actions() == []           # never pressed anything


def test_operator_abort_stops_the_loop_immediately():
    """stop_requested short-circuits run_live's while condition on the very
    first check — never-strand must never keep a genuine operator stop
    spinning through redispatch attempts."""
    clock, sleeper, box = _clock_pair()
    driver_calls = []

    def driver(runner):
        driver_calls.append(1)

    r = _runner(clock=clock, sleeper=sleeper, tail=_EmptyTail(), driver=driver)
    r._needs_redispatch = True
    r.stop_requested = True
    r.run_live(duration_s=90.0, poll_interval_s=1.0)
    assert driver_calls == []


def test_persistent_strand_never_overflows_backoff():
    """OVERFLOW-SAFE (council blocker fix 2026-07-07, boundaries lens). A
    genuine unending strand climbs `_redispatch_attempts` without bound (it
    resets ONLY on a COMPLETED run or a new journal event, neither of which
    fires while the ship idles). The backoff exponent MUST be clamped: an
    UNCLAMPED 2**(attempts-1) raises OverflowError at ~attempt 1025, which
    run_live's CrashParked handler turns into panic+stop = the EXACT strand
    this guard exists to prevent, inside one overnight session. At an absurd
    attempt count the call must still return, not raise, and the backoff must
    pin at the cap (infinite loud bounded idle -- never a terminal stop)."""
    clock, sleeper, box = _clock_pair()
    calls = []

    def driver(runner):
        calls.append(1)
        runner._needs_redispatch = True          # never resolves

    r = _runner(clock=clock, sleeper=sleeper, tail=_EmptyTail(), driver=driver)
    # Jump straight to a count whose UNCLAMPED exponent (2**4999) would raise
    # OverflowError converting to float. The clamp must make this a no-drama
    # capped attempt.
    r._redispatch_attempts = 5000
    r._needs_redispatch = True
    r._redispatch_next_t = 0.0
    r._maybe_redispatch()                         # must NOT raise
    assert calls == [1]
    assert r._redispatch_attempts == 5001
    # Next window is exactly the cap ahead -- no overflow, no runaway value.
    assert r._redispatch_next_t == pytest.approx(box["t"] + 30.0, abs=0.01)


def test_driver_exception_rearms_and_retries_next_window(capsys):
    """RE-ARM (council blocker fix 2026-07-07, failure-recovery lens). A driver
    that RAISES must NOT permanently silence never-strand. `_needs_redispatch`
    is cleared before the call (iv); on an exception it MUST be re-set so the
    next backoff window retries. Without the re-arm, ONE transient driver fault
    strands the ship for the rest of the session. Driver raises once, succeeds
    once -> it is called TWICE, loudly, and the strand is never silently
    abandoned."""
    clock, sleeper, box = _clock_pair()
    records = []
    calls = {"n": 0}

    def driver(runner):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient driver fault")
        # second call resolves cleanly (does not re-queue)

    r = _runner(clock=clock, sleeper=sleeper, tail=_EmptyTail(), driver=driver,
                record_sink=lambda n, p: records.append((n, p)))
    r._needs_redispatch = True
    r.run_live(duration_s=30.0, poll_interval_s=1.0)
    assert calls["n"] == 2, "driver exception permanently silenced never-strand"
    out = capsys.readouterr().out
    assert "re-armed" in out                      # LOUD, not silent
    assert any(n == "RedispatchDriverError" for n, _ in records)
    assert r._needs_redispatch is False           # second call resolved it
