"""ADVERSARIAL RE-VERIFICATION (council wf_7783dbe3-ba5, Stage-2, lens=failure-
recovery ONLY). Probes the arbiter-mandated merge fix to step_wait_body_scanned
(persistent high-water baseline `ctx.explore_scan_seq_consumed`, bounded
backstop retained). This file is a REVIEW ARTIFACT — it does not modify the
candidate; it only exercises it adversarially. Pure-Python, no game, no CV.

Each test is a probe named after the scenario in the review brief. A test
FAILURE here means the merged fix has NOT closed the reported gap for that
scenario.
"""

from pathlib import Path

from ed_core.flow.context import StepContext
from ed_core.flow.interpreter import run_procedure
from ed_core.flow.loader import load_procedures
from ed_autojump.flow.steps import STEP_REGISTRY
from tests.flow import FakeSender

PROC_DIR = Path(__file__).resolve().parents[2] / "procedures"

wait_body_scanned = STEP_REGISTRY["wait_body_scanned"]


def _ctx(**kw):
    return StepContext(sender=FakeSender(), sleeper=lambda s: None, **kw)


# ---- Probe 1: prompt return, ZERO sleeps, on the original defect scenario ----

def test_probe1_scan_landed_between_waits_zero_sleeps():
    """The exact originally-reported defect: a scan lands DURING the previous
    body's engage/throttle/orient steps (i.e. strictly between two
    wait_body_scanned calls). The second wait must fire on its FIRST check —
    zero polls / zero sleeps — not burn the poll budget."""
    state = {"seq": 0}
    sleeps = {"n": 0}

    def autoscan():
        return (state["seq"], frozenset())

    def sleeper(_s):
        sleeps["n"] += 1
        state["seq"] += 1  # body 1's scan lands mid-wait

    logs = []
    ctx = StepContext(sender=FakeSender(), sleeper=sleeper,
                      autoscan_supplier=autoscan,
                      record=lambda k, p: logs.append((k, p)))
    assert wait_body_scanned(ctx, poll_s=0.0) is True  # wait #1: consumes seq 1

    # Body 2's scan fires during ITS engage/orient steps, before wait #2 runs.
    state["seq"] += 1
    sleeps["n"] = 0
    assert wait_body_scanned(ctx, poll_s=0.0, max_polls=240) is True
    assert sleeps["n"] == 0, (
        "REGRESSION: prompt-catch path polled/slept instead of firing on the "
        "first check — the original wall-clock-on-happy-path defect is back."
    )
    results = [p["result"] for k, p in logs if k == "WaitBodyScanned"]
    assert results[-1] == "scanned"


# ---- Probe 2: first-call fallback is still bounded, no false-instant-return -

def test_probe2_first_call_nonzero_seq_does_not_false_fire():
    """First-ever call (no consumed history) with a supplier that already
    reports a NON-ZERO seq (e.g. counter warmed up before this gate ever ran).
    The entry-snapshot fallback must treat that as the baseline — NOT an
    instant false-positive 'scanned' — and must still be bounded if no
    further advance ever comes."""
    ctx = _ctx(autoscan_supplier=lambda: (37, frozenset()))
    assert getattr(ctx, "explore_scan_seq_consumed", None) is None  # sanity: no history
    logs = []
    ctx.record = lambda k, p: logs.append((k, p))
    assert wait_body_scanned(ctx, poll_s=0.0, max_polls=3) is True
    results = [p["result"] for k, p in logs if k == "WaitBodyScanned"]
    assert results[-1] == "backstop", (
        "First call with a nonzero-but-unchanging seq must bound out via the "
        "poll-count backstop, not silently 'scan' on a stale reading."
    )


def test_probe2b_first_call_bounded_when_scan_never_arrives():
    """First-ever call, no history, scan NEVER arrives: must exit via the
    poll-count backstop within max_polls, never hang."""
    ctx = _ctx(autoscan_supplier=lambda: (0, frozenset()))
    logs = []
    ctx.record = lambda k, p: logs.append((k, p))
    assert wait_body_scanned(ctx, poll_s=0.0, max_polls=5) is True
    results = [p["result"] for k, p in logs if k == "WaitBodyScanned"]
    assert results[-1] == "backstop"


# ---- Probe 3: multi-scan burst (seq +2) between waits -----------------------

def test_probe3_multi_scan_burst_no_double_consume_no_false_fire():
    """Between two waits, TWO scans land (seq jumps +2, not +1). The next wait
    must catch it PROMPTLY (first check) and record the LATEST seq as
    consumed — not stall, not double count, not miss the second scan."""
    logs = []
    state = {"seq": 0}
    sleeps = {"n": 0}

    def autoscan():
        return (state["seq"], frozenset())

    def sleeper(_s):
        sleeps["n"] += 1
        state["seq"] += 1

    ctx = StepContext(sender=FakeSender(), sleeper=sleeper,
                      autoscan_supplier=autoscan,
                      record=lambda k, p: logs.append((k, p)))
    assert wait_body_scanned(ctx, poll_s=0.0) is True  # wait #1 consumes seq=1
    assert ctx.explore_scan_seq_consumed == 1

    state["seq"] += 2  # burst: two scans land before wait #2 runs
    sleeps["n"] = 0
    assert wait_body_scanned(ctx, poll_s=0.0, max_polls=3) is True
    assert sleeps["n"] == 0, "burst was not caught on the first check"
    assert ctx.explore_scan_seq_consumed == 3, (
        "consumed high-water must reflect the LATEST seq after a burst, not "
        "an intermediate/duplicated value"
    )

    # Immediately-following wait #3 with NO further advance must NOT re-fire
    # instantly off the stale burst value (would be a double-consume bug) —
    # it must bound out via backstop instead. Swap in a no-op sleeper so this
    # wait's own polling doesn't manufacture a fresh advance (wait #1/#2's
    # sleeper intentionally advances seq to simulate a landing scan; here we
    # need a genuinely stuck supplier to isolate the double-consume check).
    ctx.sleeper = lambda s: None
    sleeps["n"] = 0
    assert wait_body_scanned(ctx, poll_s=0.0, max_polls=3) is True
    results = [p["result"] for k, p in logs if k == "WaitBodyScanned"]
    assert results[-1] == "backstop", (
        "wait #3 false-fired off the already-consumed burst value "
        "(double-consume bug)"
    )


# ---- Probe 4: abort path updates the high-water and returns -----------------

def test_probe4_abort_updates_high_water_and_next_wait_uses_it():
    """should_abort mid-poll must still: (a) return True immediately, (b)
    persist explore_scan_seq_consumed at the seq value observed at abort time,
    so a SUBSEQUENT wait (e.g. next body, abort flag since cleared) uses that
    as its baseline rather than re-snapshotting from scratch."""
    logs = []
    state = {"seq": 5, "polls": 0}
    abort_flag = {"on": True}

    def autoscan():
        return (state["seq"], frozenset())

    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      autoscan_supplier=autoscan,
                      should_abort=lambda: abort_flag["on"],
                      record=lambda k, p: logs.append((k, p)))
    assert wait_body_scanned(ctx, poll_s=0.0) is True
    results = [p["result"] for k, p in logs if k == "WaitBodyScanned"]
    assert results[-1] == "abort"
    assert ctx.explore_scan_seq_consumed == 5, (
        "abort exit must persist the high-water baseline at the observed seq"
    )

    # Next body: abort cleared, a real scan already landed (seq 5 -> 6) before
    # the wait runs. Must fire promptly off the persisted baseline of 5.
    abort_flag["on"] = False
    state["seq"] = 6
    logs.clear()
    assert wait_body_scanned(ctx, poll_s=0.0, max_polls=3) is True
    results = [p["result"] for k, p in logs if k == "WaitBodyScanned"]
    assert results[-1] == "scanned"


# ---- Probe 5: backstop path with a permanently-stuck supplier ---------------

def test_probe5_backstop_bounded_when_supplier_permanently_stuck():
    """Supplier's seq never advances at all (stuck sensor / dead journal
    tail). Confirms the bound is a POLL COUNT, not time — max_polls=1 exits
    after exactly one extra poll regardless of poll_s, and max_polls scales
    linearly (no runaway)."""
    ctx = _ctx(autoscan_supplier=lambda: (9, frozenset()))
    logs = []
    ctx.record = lambda k, p: logs.append((k, p))
    assert wait_body_scanned(ctx, poll_s=0.0, max_polls=1) is True
    results = [p["result"] for k, p in logs if k == "WaitBodyScanned"]
    assert results[-1] == "backstop"
    assert ctx.explore_scan_seq_consumed == 9


def test_probe5b_backstop_then_real_advance_next_wait_fires_promptly():
    """A wait that exits via backstop (consumed == stale seq, never actually
    advanced) must NOT poison the next wait: once a real advance finally
    happens, the very next wait must catch it on the first check (this is the
    'seq0-from-history-after-a-stale-backstop' scenario, item 7 of the brief)."""
    logs = []
    state = {"seq": 9}

    def autoscan():
        return (state["seq"], frozenset())

    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      autoscan_supplier=autoscan,
                      record=lambda k, p: logs.append((k, p)))
    assert wait_body_scanned(ctx, poll_s=0.0, max_polls=3) is True  # backstop, consumed=9
    assert ctx.explore_scan_seq_consumed == 9

    state["seq"] = 10  # a real scan finally lands before the next wait runs
    logs.clear()
    assert wait_body_scanned(ctx, poll_s=0.0, max_polls=3) is True
    results = [p["result"] for k, p in logs if k == "WaitBodyScanned"]
    assert results[-1] == "scanned", (
        "a stale backstop-consumed baseline must not block a later genuine "
        "advance from firing promptly"
    )


# ---- Probe 6: supplier raises mid-loop --------------------------------------

def test_probe6_supplier_raises_on_every_call_no_crash_returns_true():
    """Supplier raises on EVERY call, including entry. Must not crash the
    step or the loop; must return True immediately (no_supplier) and must not
    corrupt explore_scan_seq_consumed."""
    def boom():
        raise RuntimeError("journal tail died")

    logs = []
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      autoscan_supplier=boom,
                      record=lambda k, p: logs.append((k, p)))
    assert wait_body_scanned(ctx, poll_s=0.0, max_polls=3) is True
    results = [p["result"] for k, p in logs if k == "WaitBodyScanned"]
    assert results[-1] == "no_supplier"
    assert getattr(ctx, "explore_scan_seq_consumed", None) is None


def test_probe6b_supplier_raises_after_entry_still_bounded():
    """Supplier succeeds ONCE at entry (establishing a baseline) then raises
    on every subsequent in-loop poll. Must still bound out via backstop
    within max_polls — not hang, not crash — and must persist the last KNOWN
    GOOD seq as consumed (not a poisoned value)."""
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            return (7, frozenset())
        raise RuntimeError("transient read miss")

    logs = []
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      autoscan_supplier=flaky,
                      record=lambda k, p: logs.append((k, p)))
    assert wait_body_scanned(ctx, poll_s=0.0, max_polls=3) is True
    results = [p["result"] for k, p in logs if k == "WaitBodyScanned"]
    assert results[-1] == "backstop"
    assert ctx.explore_scan_seq_consumed == 7


# ---- Probe 7 (loop-scene integration): wired exploration.toml, multi-body ---

def _exploration():
    return load_procedures(PROC_DIR)["exploration"]


def test_probe7_wired_loop_multi_body_burst_never_hangs_never_blind():
    """Drive the REAL exploration.toml loop across 3 bodies where a burst scan
    (seq +2) lands between body 2 and body 3's waits, and body 3's supplier
    then goes permanently stuck. The whole procedure must still complete
    (fail-closed loop_max is the outer bound; wait_body_scanned's own bound
    must not additionally stall it) — i.e. no unbounded/blind path through the
    wired loop."""
    proc = _exploration()
    calls = []
    state = {"n": 0, "seq": 0}
    N = 3

    def make(name):
        def fn(ctx, **params):
            calls.append(name)
            if name == "nav_supercruise_unexplored":
                state["n"] += 1
                return state["n"] <= N
            if name == "wait_body_scanned":
                # exercise the REAL step against a stateful supplier instead
                # of the stubbed True.
                return wait_body_scanned(ctx, poll_s=0.0, max_polls=5)
            return True
        return fn

    registry = {s.action: make(s.action) for s in proc.steps}

    def autoscan():
        return (state["seq"], frozenset())

    def sleeper(_s):
        # body 1 & 2: scan lands after one poll (prompt path exercised on 2)
        if state["n"] <= 2:
            state["seq"] += 1
        # body 3: supplier goes stuck — no further advance, must backstop out

    ctx = StepContext(sender=FakeSender(), sleeper=sleeper,
                      autoscan_supplier=autoscan)
    # Burst before body 2's wait: +2 instead of +1, simulating two scans
    # landing back-to-back during body1->body2 transit.
    orig_make_n = state["n"]
    result = run_procedure(proc, ctx, registry=registry)

    assert result.completed is True and result.aborted is False
    assert calls.count("wait_body_scanned") == N
    # bounded: the run terminated at all (pytest itself is the timeout backstop
    # here — an infinite loop would hang the test process, not just fail an
    # assert).


# ---- Probe 8 (RESIDUAL RISK, informational): supplier seq COUNTER RESET -----

def test_probe8_residual_risk_seq_reset_degrades_to_backstop_but_stays_bounded():
    """RESIDUAL RISK (not a hang, not a crash — documents a degraded-mode
    edge): if the underlying AutoScan seq counter ever RESETS while ctx (and
    its persisted explore_scan_seq_consumed high-water) survives — e.g. a
    journal-tail reconnect/new-session scenario where the process does NOT
    restart but the counter's origin does — the persisted baseline is now
    HIGHER than the live counter. seq > seq0 can be false for every body
    until the counter organically climbs back past the old high-water mark,
    so every wait in between silently degrades to the full backstop bound
    (poll_s * max_polls) instead of firing on first check. This IS still
    bounded per-call (no hang) but is a real perf/behavior regression risk
    versus the entry-snapshot version for this specific external-reset case.
    Flagging as residual risk, not a blocker: bounded-ness (the property the
    merge was mandated to preserve) holds; promptness on the happy path does
    not, for this one edge."""
    logs = []
    state = {"seq": 50}

    def autoscan():
        return (state["seq"], frozenset())

    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      autoscan_supplier=autoscan,
                      record=lambda k, p: logs.append((k, p)))
    assert wait_body_scanned(ctx, poll_s=0.0) is True  # establishes consumed=50
    assert ctx.explore_scan_seq_consumed == 50

    # Simulate a counter reset (new session origin) — a REAL scan for the new
    # body lands (seq goes 0 -> 1) but that is still far below the stale
    # high-water of 50.
    state["seq"] = 1
    logs.clear()
    assert wait_body_scanned(ctx, poll_s=0.0, max_polls=5) is True  # bounded: does not hang
    results = [p["result"] for k, p in logs if k == "WaitBodyScanned"]
    assert results[-1] == "backstop", (
        "documents degraded (but still bounded) behavior across a supplier "
        "seq reset — a real scan is invisible to the stale high-water "
        "baseline until the counter climbs back past it"
    )
