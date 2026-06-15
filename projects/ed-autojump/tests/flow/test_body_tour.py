"""body_tour subsystem tests: step behavior + _apply_state latch/reset +
_make_context wiring. No game, no real sleeps.

The gate is driven by SUPPLIER SNAPSHOTS (PD1), not event_waiters, so the
fakes here back the suppliers with mutable boxes and use a scripted
event_waiter/sleeper that advances those boxes to simulate the journal
landing in _apply_state. The waiter return value is IGNORED by the step.
"""

from __future__ import annotations

from types import SimpleNamespace

from ed_autojump.flow import StepContext
from ed_autojump.flow.dispatcher import FlowRunner
from ed_autojump.flow.steps import (
    INPUT_EXCLUSIVE_ACTIONS,
    STEP_REGISTRY,
)
from ed_explore.steps_body_tour import step_body_tour
from ed_core.journal.events import parse_event
from tests.flow import FakeSender


class _Clock:
    """Monotonic fake: each call advances by `step` seconds."""

    def __init__(self, step=0.1):
        self.t = 0.0
        self.step = step

    def __call__(self):
        self.t += self.step
        return self.t


class _Box:
    """Mutable journal-state box the suppliers read and the fake waiter writes.
    Models exactly what _apply_state maintains for the tour."""

    def __init__(self):
        self.autoscan_seq = 0
        self.autoscan_bodies: set[str] = set()
        self.scex_seq = 0
        self.drop_seq = 0
        self.fss = False

    def scan(self, body_name: str):
        self.autoscan_bodies.add(body_name)
        self.autoscan_seq += 1

    def drop(self):
        self.drop_seq += 1

    def exit(self):
        self.scex_seq += 1


def _records():
    out = []
    return out, (lambda name, payload: out.append((name, payload)))


def _types(records):
    return [n for n, _ in records]


def _payloads(records, name):
    return [p for n, p in records if n == name]


def _ctx(sender, box, *, enabled=True, on_each_pump=None, clock=None,
         records_fn=None, status=None, system=None, guard=None,
         dwell_s=2.0, max_bodies=5, max_rows=8, orbit_timeout_s=120.0,
         k_start=1, should_abort=None, no_waiter=False, min_bodies=0, fss_count=0):
    """Build a StepContext wired to `box`. `on_each_pump()` runs on every gate
    poll (the place the real journal would advance a latch)."""
    pumps = {"n": 0}

    def _waiter(name, t):
        pumps["n"] += 1
        if on_each_pump is not None:
            on_each_pump(pumps["n"])
        return False                # return value IGNORED by the step

    def _sleeper(_s):
        # In no-waiter mode the gate pumps via the sleeper instead.
        if no_waiter:
            pumps["n"] += 1
            if on_each_pump is not None:
                on_each_pump(pumps["n"])

    return StepContext(
        sender=sender,
        clock=clock or _Clock(),
        sleeper=_sleeper,
        status_supplier=(lambda: status),
        current_system_supplier=(lambda: system),
        event_waiter=(None if no_waiter else _waiter),
        should_abort=(should_abort or (lambda: False)),
        exclusive_guard=guard,
        record=records_fn,
        body_tour_enabled=enabled,
        body_tour_dwell_s=dwell_s,
        body_tour_max_bodies=max_bodies,
        body_tour_max_rows=max_rows,
        body_tour_orbit_timeout_s=orbit_timeout_s,
        body_tour_min_bodies=min_bodies,
        fss_body_count_supplier=(lambda: fss_count),
        fss_discovered_supplier=(lambda: box.fss),
        autoscan_supplier=(lambda: (box.autoscan_seq,
                                    frozenset(box.autoscan_bodies))),
        drop_seq_supplier=(lambda: box.drop_seq),
        scex_seq_supplier=(lambda: box.scex_seq),
    )


# ===========================================================================
# 1. OFF == byte-identical (criterion 1)
# ===========================================================================

def test_body_tour_off_is_noop():
    sender = FakeSender()
    box = _Box()
    records, rec = _records()
    ctx = _ctx(sender, box, enabled=False, records_fn=rec)
    assert step_body_tour(ctx) is True
    assert sender.actions() == []                  # byte-identical: zero keypresses
    assert _types(records) == ["BodyTourSkipped"]
    assert records[0][1] == {"reason": "disabled"}


def test_body_tour_min_bodies_skips_small_system():
    """min-body gate (operator 2026-06-08): a system with fewer than the
    threshold is skipped entirely (zero keypresses), the jump resumes."""
    sender = FakeSender()
    box = _Box()
    records, rec = _records()
    ctx = _ctx(sender, box, records_fn=rec, min_bodies=40, fss_count=20)
    assert step_body_tour(ctx) is True
    assert sender.actions() == []                  # skipped before any macro
    assert "BodyTourSkippedFewBodies" in _types(records)
    assert _payloads(records, "BodyTourSkippedFewBodies")[0] == {
        "body_count": 20, "min": 40}


def test_body_tour_min_bodies_tours_when_threshold_met():
    """min-body gate: a system AT the threshold is NOT skipped — the tour
    proceeds past the gate (no BodyTourSkippedFewBodies)."""
    sender = FakeSender()
    box = _Box()
    records, rec = _records()
    # fss_count == min: gate passes. max_rows=1 tours zero rows, but the point
    # is the gate did NOT short-circuit -> BodyTourComplete, not the skip.
    ctx = _ctx(sender, box, records_fn=rec, min_bodies=40, fss_count=40,
               max_rows=1)
    assert step_body_tour(ctx) is True
    assert "BodyTourSkippedFewBodies" not in _types(records)
    assert "BodyTourComplete" in _types(records)


def test_body_tour_off_reads_no_suppliers():
    """Flag OFF must not consult the journal suppliers at all."""
    sender = FakeSender()
    box = _Box()
    calls = {"autoscan": 0, "drop": 0, "scex": 0, "fss": 0}

    def _wrap(key, fn):
        def inner():
            calls[key] += 1
            return fn()
        return inner

    ctx = _ctx(sender, box, enabled=False)
    ctx.autoscan_supplier = _wrap("autoscan", ctx.autoscan_supplier)
    ctx.drop_seq_supplier = _wrap("drop", ctx.drop_seq_supplier)
    ctx.scex_seq_supplier = _wrap("scex", ctx.scex_seq_supplier)
    ctx.fss_discovered_supplier = _wrap("fss", ctx.fss_discovered_supplier)
    assert step_body_tour(ctx) is True
    assert calls == {"autoscan": 0, "drop": 0, "scex": 0, "fss": 0}


# ===========================================================================
# 2. Per-body orbit happy path (D1, criterion 3)
# ===========================================================================

def test_body_tour_orbit_happy_path():
    sender = FakeSender()
    box = _Box()
    records, rec = _records()

    # On the FIRST gate pump, the toured body's AutoScan lands; max_bodies=1
    # so we stop after one body.
    def pump(n):
        if n == 1:
            box.scan("Ellaidst ES-N c6-100 A")

    ctx = _ctx(sender, box, on_each_pump=pump, records_fn=rec, max_bodies=1)
    assert step_body_tour(ctx) is True
    # The row-k macro keys fired (lock + SC-assist tail).
    acts = sender.actions()
    assert acts[0] == "FocusLeftPanel"
    assert acts[-1] == "FocusLeftPanel"
    assert "UI_Right" in acts                       # lock+SC, not a plain lock
    scanned = _payloads(records, "BodyTourBodyScanned")
    assert len(scanned) == 1
    assert scanned[0]["new"] == ["Ellaidst ES-N c6-100 A"]
    assert "BodyTourComplete" in _types(records)


def test_body_tour_dwell_recorded():
    """After a confirmed body the step sleeps body_tour_dwell_s (pacing)."""
    sender = FakeSender()
    box = _Box()
    slept = []

    def pump(n):
        if n == 1:
            box.scan("Body A")

    ctx = _ctx(sender, box, on_each_pump=pump, max_bodies=1, dwell_s=2.0)
    # Wrap the sleeper to capture the dwell call.
    orig = ctx.sleeper
    ctx.sleeper = lambda s: (slept.append(s), orig(s))[1]
    assert step_body_tour(ctx) is True
    assert 2.0 in slept


# ===========================================================================
# 3. De-dup: already-seen body (the arrival star) is not re-dwelled (D5)
# ===========================================================================

def test_body_tour_dedup_already_seen():
    sender = FakeSender()
    box = _Box()
    records, rec = _records()
    # Seed the seen-set with a body; the gate's AutoScan re-adds the SAME name
    # (seq bumps but `new` is empty) -> not a fresh body.
    box.autoscan_bodies.add("Arrival Star")
    box.autoscan_seq = 5

    def pump(n):
        if n == 1:
            box.scan("Arrival Star")             # same name -> seq++ but new={}

    ctx = _ctx(sender, box, on_each_pump=pump, records_fn=rec,
               max_rows=2, k_start=1)
    assert step_body_tour(ctx) is True
    assert "BodyTourAlreadySeen" in _types(records)
    assert _payloads(records, "BodyTourBodyScanned") == []


# ===========================================================================
# 4. Station-drop recovery (D2, PD7)
# ===========================================================================

def test_body_tour_station_drop_recovery():
    sender = FakeSender()
    box = _Box()
    records, rec = _records()
    # in_supercruise=False so the re-engage step actually presses Supercruise
    # (its short-circuit only triggers when already in SC). event_waiter is
    # wired but step_engage_supercruise sees in_supercruise False and presses.
    status = SimpleNamespace(in_supercruise=False)

    def pump(n):
        # First body's gate: a station drop -> SupercruiseExit fires.
        if n == 1:
            box.drop()
            box.exit()
        elif n == 2:
            # SC re-acquired during the re-engage's poll: step_engage_supercruise
            # presses Supercruise, then returns True when in_supercruise flips.
            status.in_supercruise = True

    # max_rows=2 -> exactly row 1 (k_start) is toured, then row>=max_rows stops;
    # the re-engage must run on the station drop.
    ctx = _ctx(sender, box, on_each_pump=pump, records_fn=rec,
               status=status, max_rows=2)
    assert step_body_tour(ctx) is True
    assert "BodyTourStationDrop" in _types(records)
    reeng = _payloads(records, "BodyTourReengage")
    assert reeng and reeng[0]["ok"] is True
    assert "Supercruise" in sender.actions()        # re-engage pressed
    assert _payloads(records, "BodyTourBodyScanned") == []   # station != body


def test_body_tour_drop_alone_does_not_reengage():
    """PD7 regression: a drop WITHOUT a matching SupercruiseExit must NOT
    trigger the re-engage (the drop fires ~5s before the exit; re-engaging
    early would no-op in SC then drop anyway). The gate keeps polling until
    the backstop fires."""
    sender = FakeSender()
    box = _Box()
    records, rec = _records()

    def pump(n):
        if n == 1:
            box.drop()                  # ONLY the drop, never the exit

    # Short backstop + stepping clock so the gate times out instead of spinning.
    # max_rows=2 -> exactly row 1 (k_start) is toured before row>=max_rows stops.
    ctx = _ctx(sender, box, on_each_pump=pump, records_fn=rec,
               clock=_Clock(step=1.0), orbit_timeout_s=3.0, max_rows=2)
    assert step_body_tour(ctx) is True
    assert "Supercruise" not in sender.actions()     # never re-engaged
    assert "BodyTourBodyTimeout" in _types(records)   # fell to the backstop


def test_body_tour_reengage_failure_returns_true():
    """Re-engage fails (Supercruise unbound) -> tour ends True, jump resumes."""
    sender = FakeSender(unbound={"Supercruise"})
    box = _Box()
    records, rec = _records()
    status = SimpleNamespace(in_supercruise=False)

    def pump(n):
        if n == 1:
            box.drop()
            box.exit()

    ctx = _ctx(sender, box, on_each_pump=pump, records_fn=rec,
               status=status, max_rows=2)
    assert step_body_tour(ctx) is True
    reeng = _payloads(records, "BodyTourReengage")
    assert reeng and reeng[0]["ok"] is False


# ===========================================================================
# 5. Loose-gate v1 limitation (PD6 adversarial — documented)
# ===========================================================================

def test_body_tour_loose_gate_unrelated_autoscan():
    """KNOWN v1 LIMITATION: the gate is 'ANY new AutoScan since the lock
    snapshot'. If an UNRELATED body auto-scans mid-orbit, the count advances
    on it. A future tightening would match BodyName against the locked
    Destination (M6-unverified for planets). This test PINS the loose
    behavior so the limitation is explicit in code."""
    sender = FakeSender()
    box = _Box()
    records, rec = _records()

    def pump(n):
        if n == 1:
            box.scan("Some Unrelated Moon")     # NOT the locked body

    ctx = _ctx(sender, box, on_each_pump=pump, records_fn=rec, max_bodies=1)
    assert step_body_tour(ctx) is True
    scanned = _payloads(records, "BodyTourBodyScanned")
    assert len(scanned) == 1                        # counted the unrelated scan
    assert scanned[0]["new"] == ["Some Unrelated Moon"]


# ===========================================================================
# 6. Backstop / exhaustion / abort (criterion 4 + fail-safes)
# ===========================================================================

def test_body_tour_body_timeout_backstop():
    sender = FakeSender()
    box = _Box()                                    # no scans ever fire
    records, rec = _records()
    ctx = _ctx(sender, box, records_fn=rec, clock=_Clock(step=1.0),
               orbit_timeout_s=3.0, max_rows=2)
    assert step_body_tour(ctx) is True
    assert "BodyTourBodyTimeout" in _types(records)
    assert "BodyTourComplete" in _types(records)


def test_body_tour_exhaustion_by_max_bodies():
    """max_bodies caps the tour; resume reaches target_next_route in the proc
    integration test below. Here: a fresh scan every gate -> stops after 2."""
    sender = FakeSender()
    box = _Box()
    records, rec = _records()
    counter = {"i": 0}

    def pump(n):
        # Every gate's FIRST pump lands a NEW body for the current row.
        counter["i"] += 1
        box.scan(f"Body {counter['i']}")

    ctx = _ctx(sender, box, on_each_pump=pump, records_fn=rec,
               max_bodies=2, max_rows=8, dwell_s=0.0)
    assert step_body_tour(ctx) is True
    assert len(_payloads(records, "BodyTourBodyScanned")) == 2
    done = _payloads(records, "BodyTourComplete")
    assert done and done[0]["bodies_toured"] == 2


def test_body_tour_consecutive_nonbody_stop():
    """3 consecutive timeouts -> early exit via the 'past the bodies' heuristic."""
    sender = FakeSender()
    box = _Box()
    records, rec = _records()
    ctx = _ctx(sender, box, records_fn=rec, clock=_Clock(step=1.0),
               orbit_timeout_s=2.0, max_rows=20)
    assert step_body_tour(ctx) is True
    assert "BodyTourNonBodyStop" in _types(records)


def test_body_tour_aborts_on_should_abort():
    sender = FakeSender()
    box = _Box()
    records, rec = _records()
    flag = {"abort": False}

    def pump(n):
        flag["abort"] = True                # abort flips mid-gate

    ctx = _ctx(sender, box, on_each_pump=pump, records_fn=rec,
               should_abort=lambda: flag["abort"], max_rows=5)
    assert step_body_tour(ctx) is True
    assert "BodyTourAborted" in _types(records)
    assert _payloads(records, "BodyTourBodyScanned") == []


# ===========================================================================
# 7. First-row local-star identity skip (criterion 2 layer 2)
# ===========================================================================

def test_body_tour_first_row_local_star_skipped():
    sender = FakeSender()
    box = _Box()
    records, rec = _records()
    # Destination reads as the local primary star (bare system name).
    dest = SimpleNamespace(name="Acihaut", body=2, system=10)
    status = SimpleNamespace(in_supercruise=True, destination=dest)
    ctx = _ctx(sender, box, records_fn=rec, status=status, system="Acihaut",
               max_rows=2)
    assert step_body_tour(ctx) is True
    assert "BodyTourSkipLocalStar" in _types(records)
    # No dwell, no scan gate entered for the skipped row.
    assert _payloads(records, "BodyTourBodyScanned") == []


# ===========================================================================
# 8. Self-guarding (D6 / PD3 / PD8)
# ===========================================================================

class _SpyGuard:
    """Records held depth over time; factory + context-manager in one."""

    def __init__(self):
        self.depth = 0
        self.max_depth = 0
        self.enters = 0

    def __call__(self):
        return self

    def __enter__(self):
        self.depth += 1
        self.enters += 1
        self.max_depth = max(self.max_depth, self.depth)
        return self

    def __exit__(self, *exc):
        self.depth -= 1
        return False


def test_body_tour_self_guards_each_body():
    sender = FakeSender()
    box = _Box()
    guard = _SpyGuard()
    holds_during_gate = []

    def pump(n):
        # While the gate polls, the guard must be RELEASED (depth 0).
        holds_during_gate.append(guard.depth)
        if n == 1:
            box.scan("Body A")

    ctx = _ctx(sender, box, on_each_pump=pump, guard=guard, max_bodies=1)
    assert step_body_tour(ctx) is True
    assert guard.enters >= 1                        # guarded the per-body macro
    assert all(d == 0 for d in holds_during_gate)   # released during the gate


# ===========================================================================
# 9. IDENTITY targeting (task #45) — read the panel, target by NAME not row
# ===========================================================================

class _FakeNavReader:
    """Stand-in for NavPanelReader: `.parse` returns a fixed body list (bypasses
    OCR). The real `next_unexplored` inside the step does the scanned-set filter."""

    def __init__(self, bodies):
        self._bodies = bodies

    def parse(self, frame, system):
        return list(self._bodies)


def _identity_ctx(sender, box, bodies, *, on_each_pump=None, records_fn=None,
                  system="Test Sys", reader=None, grabber=None, **kw):
    ctx = _ctx(sender, box, on_each_pump=on_each_pump, records_fn=records_fn,
               system=system, **kw)
    ctx.nav_panel_reader = reader if reader is not None else _FakeNavReader(bodies)
    ctx.nav_panel_grabber = grabber if grabber is not None else (lambda: object())
    return ctx


def test_body_tour_identity_targets_unexplored_in_order():
    """The two unexplored planets are toured by NAME (the arrival star, already
    in the scanned-set, is skipped), then the tour ends when none remain."""
    from ed_vision.navpanel_reader import NavBody
    sender = FakeSender()
    box = _Box()
    records, rec = _records()
    box.autoscan_bodies.add("Test Sys A")           # arrival star pre-scanned
    bodies = [
        NavBody(row_index=0, name="Test Sys A", designator="A", raw=""),
        NavBody(row_index=1, name="Test Sys A 1", designator="A 1", raw=""),
        NavBody(row_index=2, name="Test Sys A 2", designator="A 2", raw=""),
    ]

    def pump(n):
        # Each gate's first pump scans the lowest still-unscanned planet — which
        # is exactly the body identity selection just targeted.
        if "Test Sys A 1" not in box.autoscan_bodies:
            box.scan("Test Sys A 1")
        elif "Test Sys A 2" not in box.autoscan_bodies:
            box.scan("Test Sys A 2")

    ctx = _identity_ctx(sender, box, bodies, on_each_pump=pump, records_fn=rec,
                        dwell_s=0.0)
    assert step_body_tour(ctx) is True
    targets = _payloads(records, "BodyTourTarget")
    assert targets == [
        {"row": 1, "body": "Test Sys A 1"},
        {"row": 2, "body": "Test Sys A 2"},
    ]
    # Both planets registered as freshly scanned, then a clean no-unexplored end.
    assert len(_payloads(records, "BodyTourBodyScanned")) == 2
    assert "BodyTourNoUnexplored" in _types(records)
    assert "BodyTourSkipLocalStar" not in _types(records)   # blind-only skip off


def test_body_tour_identity_no_unexplored_immediate_end():
    """Every panel body already scanned -> the tour ends before any keypress."""
    from ed_vision.navpanel_reader import NavBody
    sender = FakeSender()
    box = _Box()
    records, rec = _records()
    box.autoscan_bodies.update({"Test Sys A", "Test Sys A 1"})
    bodies = [
        NavBody(row_index=0, name="Test Sys A", designator="A", raw=""),
        NavBody(row_index=1, name="Test Sys A 1", designator="A 1", raw=""),
    ]
    ctx = _identity_ctx(sender, box, bodies, records_fn=rec)
    assert step_body_tour(ctx) is True
    assert sender.actions() == []                   # nothing to tour, no macro
    assert "BodyTourNoUnexplored" in _types(records)


def test_body_tour_identity_read_failure_fails_open():
    """A grabber/OCR exception ends the tour cleanly (True), never raises."""
    sender = FakeSender()
    box = _Box()
    records, rec = _records()

    def boom():
        raise RuntimeError("no tesseract")

    ctx = _identity_ctx(sender, box, [], records_fn=rec, grabber=boom)
    assert step_body_tour(ctx) is True
    assert "BodyTourReadFail" in _types(records)
    assert sender.actions() == []


def test_body_tour_nullcontext_when_no_guard():
    """exclusive_guard=None -> the nullcontext fallback runs without error."""
    sender = FakeSender()
    box = _Box()

    def pump(n):
        if n == 1:
            box.scan("Body A")

    ctx = _ctx(sender, box, on_each_pump=pump, guard=None, max_bodies=1)
    assert step_body_tour(ctx) is True


# ===========================================================================
# 9. Advisory FSS log (PD5)
# ===========================================================================

def test_body_tour_advisory_fss_logs_not_blocks():
    sender = FakeSender()
    box = _Box()                                    # box.fss stays False
    records, rec = _records()

    def pump(n):
        if n == 1:
            box.scan("Body A")

    ctx = _ctx(sender, box, on_each_pump=pump, records_fn=rec, max_bodies=1)
    assert step_body_tour(ctx) is True              # ran despite fss False
    fss = _payloads(records, "BodyTourFssState")
    assert fss and fss[0]["fss_discovered"] is False


# ===========================================================================
# 10. _apply_state latch + reset (FlowRunner, real parsed events)
# ===========================================================================

def _runner():
    return FlowRunner(
        procedures={}, sender=FakeSender(), clock=lambda: 0.0,
        sleeper=lambda s: None, status_supplier=lambda: None)


def _apply(r, **fields):
    r._apply_state(parse_event(fields))


def test_apply_state_autoscan_latches_and_seq():
    r = _runner()
    _apply(r, event="Scan", timestamp="2026-06-08T00:00:00Z",
           ScanType="AutoScan", BodyName="X A", BodyID=1)
    assert r._autoscan_bodies == {"X A"}
    assert r._autoscan_seq == 1
    # A non-AutoScan Scan must NOT latch.
    _apply(r, event="Scan", timestamp="2026-06-08T00:00:01Z",
           ScanType="Detailed", BodyName="X B", BodyID=2)
    assert r._autoscan_bodies == {"X A"}
    assert r._autoscan_seq == 1


def test_apply_state_fss_drop_scex_seqs():
    r = _runner()
    assert r._fss_discovered is False
    _apply(r, event="FSSDiscoveryScan", timestamp="2026-06-08T00:00:00Z",
           Progress=0.1, BodyCount=5, NonBodyCount=2,
           SystemName="Sys", SystemAddress=123)
    assert r._fss_discovered is True
    _apply(r, event="SupercruiseDestinationDrop",
           timestamp="2026-06-08T00:00:01Z", Type="Robigo Mines", MarketID=9)
    assert r._drop_seq == 1
    _apply(r, event="SupercruiseExit", timestamp="2026-06-08T00:00:02Z",
           StarSystem="Sys", BodyType="Station")
    assert r._scex_seq == 1


def test_apply_state_fsdjump_resets_system_scoped_only():
    """FSDJump resets the SYSTEM-scoped latches; _drop_seq/_scex_seq are
    monotone session counters and stay (Q2 decision)."""
    r = _runner()
    r._autoscan_bodies = {"A", "B"}
    r._autoscan_seq = 7
    r._fss_discovered = True
    r._drop_seq = 3
    r._scex_seq = 4
    _apply(r, event="FSDJump", timestamp="2026-06-08T00:00:00Z",
           StarSystem="NewSys", SystemAddress=42, StarPos=[0.0, 0.0, 0.0],
           JumpDist=10.0, FuelUsed=1.0, FuelLevel=20.0)
    assert r._autoscan_bodies == set()
    assert r._autoscan_seq == 0
    assert r._fss_discovered is False
    assert r._drop_seq == 3                          # NOT reset
    assert r._scex_seq == 4                           # NOT reset


# ===========================================================================
# 11. _make_context wiring
# ===========================================================================

def test_make_context_wires_body_tour_fields():
    r = FlowRunner(
        procedures={}, sender=FakeSender(), clock=lambda: 0.0,
        sleeper=lambda s: None, status_supplier=lambda: None,
        body_tour_enabled=True, body_tour_dwell_s=5.0,
        body_tour_max_bodies=3, body_tour_max_rows=6,
        body_tour_orbit_timeout_s=90.0)
    ctx = r._make_context()
    assert ctx.body_tour_enabled is True
    assert ctx.body_tour_dwell_s == 5.0
    assert ctx.body_tour_max_bodies == 3
    assert ctx.body_tour_max_rows == 6
    assert ctx.body_tour_orbit_timeout_s == 90.0
    # Suppliers reflect runner state LIVE.
    r._autoscan_seq = 2
    r._autoscan_bodies = {"Z"}
    r._fss_discovered = True
    r._drop_seq = 1
    r._scex_seq = 1
    assert ctx.autoscan_supplier() == (2, frozenset({"Z"}))
    assert ctx.fss_discovered_supplier() is True
    assert ctx.drop_seq_supplier() == 1
    assert ctx.scex_seq_supplier() == 1


def test_make_context_body_tour_defaults_off():
    r = FlowRunner(
        procedures={}, sender=FakeSender(), clock=lambda: 0.0,
        sleeper=lambda s: None, status_supplier=lambda: None)
    ctx = r._make_context()
    assert ctx.body_tour_enabled is False
    assert ctx.autoscan_supplier() == (0, frozenset())
    assert ctx.fss_discovered_supplier() is False
    assert ctx.drop_seq_supplier() == 0
    assert ctx.scex_seq_supplier() == 0


# ===========================================================================
# 12. Registry + exclusivity invariants
# ===========================================================================

def test_body_tour_registered():
    assert "body_tour" in STEP_REGISTRY
    assert STEP_REGISTRY["body_tour"] is step_body_tour


def test_body_tour_not_input_exclusive():
    assert "body_tour" not in INPUT_EXCLUSIVE_ACTIONS
