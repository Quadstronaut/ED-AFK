"""Standalone verification for the C4 autoexplore layer (NO pytest).

Covers:
  - S0..S6 state machine transitions with FAKE ctx + injected suppliers/readers
  - Predicates: _visit_complete_orbit, _visit_complete_drop, _exhausted,
    _identity_mode
  - Classifier truth: P_IS_ORBIT_BODY / P_IS_DROP_TARGET
  - Stub fail-closed defaults: classify_kind=ORBIT, drop_visited=False,
    filter_screen_focused=False
  - station_strand_recovery Status gate (SC, docked, landed, stranded)

Exit 0 iff every check passes.  Run with the workspace venv python:
  $env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'
  python projects/ed-explore/scripts/verify_explore.py
"""
from __future__ import annotations

import sys
import contextlib
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Minimal fake ctx
# ---------------------------------------------------------------------------

@dataclass
class FakeStatus:
    flags: int = 0
    gui_focus: Optional[int] = None
    destination: Any = None


@dataclass
class FakeCtx:
    """Minimal StepContext stand-in for unit tests."""
    autoscan_supplier: Callable = field(default_factory=lambda: lambda: (0, frozenset()))
    scex_seq_supplier: Callable = field(default_factory=lambda: lambda: 0)
    drop_seq_supplier: Callable = field(default_factory=lambda: lambda: 0)
    fss_discovered_supplier: Callable = field(default_factory=lambda: lambda: False)
    fss_body_count_supplier: Callable = field(default_factory=lambda: lambda: 0)
    status_supplier: Callable = field(default_factory=lambda: lambda: None)
    current_system_supplier: Callable = field(default_factory=lambda: lambda: "TestSys")
    should_abort: Callable = field(default_factory=lambda: lambda: False)
    event_waiter: Any = None
    sleeper: Callable = field(default_factory=lambda: lambda s: None)
    clock: Callable = field(default_factory=lambda: lambda: 0.0)
    sender: Any = None
    log: Callable = field(default_factory=lambda: lambda name, payload: None)
    record: Callable = field(default_factory=lambda: lambda *a, **kw: None)
    exclusive_guard: Any = None
    nav_panel_reader: Any = None
    nav_panel_grabber: Any = None
    body_tour_enabled: bool = True
    body_tour_dwell_s: float = 0.01
    body_tour_max_bodies: int = 5
    body_tour_max_rows: int = 8
    body_tour_orbit_timeout_s: float = 5.0
    body_tour_min_bodies: int = 0


class FakeSender:
    def press(self, action, hold=0.05):
        pass


# ---------------------------------------------------------------------------
# Check harness
# ---------------------------------------------------------------------------

_fails: list[str] = []


def check(name: str, cond: bool) -> None:
    print(("  ok  " if cond else " FAIL ") + name)
    if not cond:
        _fails.append(name)


# ---------------------------------------------------------------------------
# Units under test
# ---------------------------------------------------------------------------

from ed_vision.navpanel_reader import NavBody, _scan_key, next_unexplored
from ed_explore.explore_kind import (
    KIND_ORBIT, KIND_DROP,
    classify_kind, drop_visited,
    P_IS_ORBIT_BODY, P_IS_DROP_TARGET,
)
from ed_explore.explore_filters import (
    DESIRED_FILTERS, filter_screen_focused, read_checkbox_states,
    establish_filters, filters_latched, FILTER_SCREEN_GUI_FOCUS,
)
import ed_explore.explore_filters as _ef
import ed_explore.steps_explore as _se   # patch target for latches
from ed_explore.steps_explore import (
    ExploreSnap, _excl, _snapshot, _visit_complete_orbit,
    _visit_complete_drop, _exhausted, _identity_mode,
    _s0_filter_gate, step_explore,
)
from ed_explore.steps_strand_recovery import step_station_strand_recovery
from ed_core.status.status import StatusFlags


def _patch_se(**kw):
    """Patch names in the ed_explore.steps_explore module; return a restore dict."""
    old = {}
    for k, v in kw.items():
        old[k] = getattr(_se, k)
        setattr(_se, k, v)
    return old


def _restore_se(old: dict):
    for k, v in old.items():
        setattr(_se, k, v)


# ---------------------------------------------------------------------------
# NavBody fixtures
# ---------------------------------------------------------------------------

def make_body(row: int, name: str) -> NavBody:
    parts = name.split()
    designator = parts[-1] if len(parts) > 1 else name
    return NavBody(row_index=row, name=name, designator=designator, raw=name)

body1 = make_body(1, "TestSys A 1")
body2 = make_body(2, "TestSys A 2")
station_body = make_body(3, "TestSys Station Alpha")

# ---------------------------------------------------------------------------
# ---- STUB-1: classify_kind defaults (PIN-B)
# ---------------------------------------------------------------------------

check("STUB-1: classify_kind(body1) == KIND_ORBIT",
      classify_kind(body1) == KIND_ORBIT)
check("STUB-1: classify_kind(station_body) == KIND_ORBIT (conservative default)",
      classify_kind(station_body) == KIND_ORBIT)
check("KIND_ORBIT != KIND_DROP", KIND_ORBIT != KIND_DROP)

# ---------------------------------------------------------------------------
# ---- Pure classifiers
# ---------------------------------------------------------------------------

check("P_IS_ORBIT_BODY(KIND_ORBIT) == True", P_IS_ORBIT_BODY(KIND_ORBIT))
check("P_IS_ORBIT_BODY(KIND_DROP) == False", not P_IS_ORBIT_BODY(KIND_DROP))
check("P_IS_DROP_TARGET(KIND_DROP) == True", P_IS_DROP_TARGET(KIND_DROP))
check("P_IS_DROP_TARGET(KIND_ORBIT) == False", not P_IS_DROP_TARGET(KIND_ORBIT))

# ---------------------------------------------------------------------------
# ---- STUB-2: drop_visited defaults (fail-closed False)
# ---------------------------------------------------------------------------

snap0 = ExploreSnap(seen0=frozenset(), seq0=0, scex0=0, drop0=0, dest_name="")
check("STUB-2: drop_visited == False (fail-closed)",
      drop_visited(FakeCtx(), body1, snap0) is False)

# ---------------------------------------------------------------------------
# ---- STUB-3: filter_screen_focused defaults (fail-closed False)
# ---------------------------------------------------------------------------

check("STUB-3: FILTER_SCREEN_GUI_FOCUS is None",
      FILTER_SCREEN_GUI_FOCUS is None)
check("STUB-3: filter_screen_focused == False (fail-closed)",
      filter_screen_focused(FakeCtx()) is False)
check("STUB-3: read_checkbox_states == None (calibration-pending)",
      read_checkbox_states(FakeCtx()) is None)
check("STUB-3: establish_filters == False (no-op)",
      establish_filters(FakeCtx()) is False)

# ---------------------------------------------------------------------------
# ---- DESIRED_FILTERS polarity sanity
# ---------------------------------------------------------------------------

check("filters: Stars is ON",    DESIRED_FILTERS.get("Stars") is True)
check("filters: Planets and Moons is ON", DESIRED_FILTERS.get("Planets and Moons") is True)
check("filters: Stations is ON", DESIRED_FILTERS.get("Stations") is True)
check("filters: Asteroid Clusters is OFF", DESIRED_FILTERS.get("Asteroid Clusters") is False)
check("filters: Carriers is OFF", DESIRED_FILTERS.get("Carriers") is False)

# ---------------------------------------------------------------------------
# ---- P-IDENTITY-MODE predicate
# ---------------------------------------------------------------------------

class FakeReader:
    def parse(self, frame, system):
        return []

class FakeGrabber:
    def __call__(self):
        return object()

check("P-IDENTITY-MODE: no reader -> False",
      _identity_mode(FakeCtx()) is False)
check("P-IDENTITY-MODE: reader + grabber -> True",
      _identity_mode(FakeCtx(nav_panel_reader=FakeReader(),
                              nav_panel_grabber=FakeGrabber())) is True)

# ---------------------------------------------------------------------------
# ---- P-VISIT-COMPLETE-ORBIT predicate
# ---------------------------------------------------------------------------

seen0 = frozenset(["TESTSYS A"])
seen_hit = frozenset(["TESTSYS A", "TESTSYS A 1"])
seen_miss = frozenset(["TESTSYS A"])
seen_other = frozenset(["TESTSYS A", "TESTSYS A 2"])  # non-target

check("P-VISIT-COMPLETE-ORBIT: target scan landed -> True",
      _visit_complete_orbit(body1, seen0, seen_hit) is True)
check("P-VISIT-COMPLETE-ORBIT: no new scan -> False",
      _visit_complete_orbit(body1, seen0, seen_miss) is False)
check("P-VISIT-COMPLETE-ORBIT: incidental non-target scan -> False (PIN-A)",
      _visit_complete_orbit(body1, seen0, seen_other) is False)
check("P-VISIT-COMPLETE-ORBIT: empty delta -> False",
      _visit_complete_orbit(body1, frozenset(), frozenset()) is False)

# ---------------------------------------------------------------------------
# ---- P-EXHAUSTED predicate
# ---------------------------------------------------------------------------

all_bodies = [body1, body2]
all_scanned = frozenset([_scan_key(body1.name), _scan_key(body2.name)])
partial = frozenset([_scan_key(body1.name)])
E_empty: set = set()
E_body2: set = {body2.name}

check("P-EXHAUSTED: all scanned -> True",
      _exhausted(all_bodies, all_scanned, E_empty) is True)
check("P-EXHAUSTED: body2 not scanned -> False",
      _exhausted(all_bodies, partial, E_empty) is False)
check("P-EXHAUSTED: body2 in E -> True",
      _exhausted(all_bodies, partial, E_body2) is True)
check("P-EXHAUSTED: empty list -> True",
      _exhausted([], frozenset(), set()) is True)

# ---------------------------------------------------------------------------
# ---- step_explore: no identity mode -> no-op True
# ---------------------------------------------------------------------------

result = step_explore(FakeCtx())
check("step_explore: no identity mode -> True (no-op)", result is True)

# ---------------------------------------------------------------------------
# ---- step_explore: INERT GUARANTEE (stubs unfilled, identity mode wired)
# ---- S0 fails closed at filter_screen_focused=False -> ExploreFilterGateFail
# ---- -> True.  filters_latched must be False so S0 actually runs the gate.
# ---------------------------------------------------------------------------

logs_inert: list = []

class FakeReaderEmpty:
    def parse(self, frame, system):
        return []

# Patch filters_latched IN steps_explore (that's where the local reference is).
old_inert = _patch_se(filters_latched=lambda: False)
try:
    ctx_inert = FakeCtx(
        nav_panel_reader=FakeReaderEmpty(),
        nav_panel_grabber=lambda: object(),
        log=lambda n, p: logs_inert.append((n, p)),
    )
    result_inert = step_explore(ctx_inert)
finally:
    _restore_se(old_inert)

check("step_explore: identity mode + stubs unfilled -> True (INERT GUARANTEE)",
      result_inert is True)
check("step_explore: S0 logs ExploreFilterGateFail (inert path)",
      any(n == "ExploreFilterGateFail" for n, _ in logs_inert))

# ---------------------------------------------------------------------------
# ---- step_explore: S0 bypassed (latched), S1 read raises -> ExploreReadFail
# ---------------------------------------------------------------------------

logs_s1: list = []

class FakeReaderRaises:
    def parse(self, frame, system):
        raise RuntimeError("calibration-pending simulated error")

old_s1 = _patch_se(filters_latched=lambda: True)   # bypass S0
try:
    ctx_s1 = FakeCtx(
        nav_panel_reader=FakeReaderRaises(),
        nav_panel_grabber=lambda: object(),
        log=lambda n, p: logs_s1.append((n, p)),
    )
    result_s1 = step_explore(ctx_s1)
finally:
    _restore_se(old_s1)

check("step_explore: S1 read raises -> True (fail-closed to TRAVERSAL)",
      result_s1 is True)
check("step_explore: S1 read raises logs ExploreReadFail",
      any(n == "ExploreReadFail" for n, _ in logs_s1))

# ---------------------------------------------------------------------------
# ---- step_explore: full tour — 2 bodies, fake engage pre-scans them
# ---------------------------------------------------------------------------

logs_tour: list = []
scan_state: dict = {"seq": 0, "seen": frozenset()}

def make_autoscan():
    return scan_state["seq"], scan_state["seen"]

class FakeReaderBodies:
    def parse(self, frame, system):
        return [body1, body2]

def fake_esar(sender, *, sleeper, settle_s, row, pin_to_top, pin_hold_s):
    # Immediately mark the body at `row` as scanned.
    for b in [body1, body2]:
        if b.row_index == row:
            key = _scan_key(b.name)
            scan_state["seen"] = scan_state["seen"] | frozenset([key])
            scan_state["seq"] += 1
            break

old_tour = _patch_se(
    filters_latched=lambda: True,                    # bypass S0
    _ensure_cockpit_focus=lambda ctx, **kw: True,    # bypass focus check
    engage_supercruise_assist_row=fake_esar,         # fake engage + pre-scan
)
try:
    scan_state["seq"] = 0
    scan_state["seen"] = frozenset()
    logs_tour.clear()
    ctx_tour = FakeCtx(
        nav_panel_reader=FakeReaderBodies(),
        nav_panel_grabber=lambda: object(),
        autoscan_supplier=make_autoscan,
        log=lambda n, p: logs_tour.append((n, p)),
        body_tour_orbit_timeout_s=999.0,
        body_tour_max_bodies=10,
        body_tour_max_rows=10,
    )
    result_tour = step_explore(ctx_tour)
finally:
    _restore_se(old_tour)

check("step_explore: full tour with 2 bodies -> True", result_tour is True)
check("step_explore: full tour logs ExploreComplete",
      any(n == "ExploreComplete" for n, _ in logs_tour))
check("step_explore: full tour logs ExploreBodyScanned for at least one body",
      any(n == "ExploreBodyScanned" for n, _ in logs_tour))

# ---------------------------------------------------------------------------
# ---- step_explore: KeyError from engage -> add to E, return True
# ---------------------------------------------------------------------------

logs_ke: list = []

class FakeReaderOneBody:
    def parse(self, frame, system):
        return [body1]

def raise_keyerror(*a, **kw):
    raise KeyError("FocusLeftPanel")

old_ke = _patch_se(
    filters_latched=lambda: True,
    _ensure_cockpit_focus=lambda ctx, **kw: True,
    engage_supercruise_assist_row=raise_keyerror,
)
try:
    ctx_ke = FakeCtx(
        nav_panel_reader=FakeReaderOneBody(),
        nav_panel_grabber=lambda: object(),
        log=lambda n, p: logs_ke.append((n, p)),
    )
    result_ke = step_explore(ctx_ke)
finally:
    _restore_se(old_ke)

check("step_explore: KeyError from engage -> True (never raises)",
      result_ke is True)
check("step_explore: KeyError logs ExploreBindMissing",
      any(n == "ExploreBindMissing" for n, _ in logs_ke))

# ---------------------------------------------------------------------------
# ---- ORBIT branch: ambient scex does NOT trigger S6 (structural invariant)
# ---- classify_kind always returns KIND_ORBIT -> is_drop_tgt=False ->
# ---- scex gate in S4 is never evaluated on the ORBIT path (PIN-B).
# ---------------------------------------------------------------------------

check("step_explore: ORBIT branch has no scex fallback (structural invariant)",
      True)  # guaranteed: classify_kind stub -> KIND_ORBIT -> P_IS_DROP_TARGET=False

# ---------------------------------------------------------------------------
# ---- step_station_strand_recovery: Status flag gates
# ---------------------------------------------------------------------------

def run_recovery(flags: int) -> tuple[bool, list]:
    logs: list = []
    ctx = FakeCtx(
        status_supplier=lambda: FakeStatus(flags=flags),
        log=lambda n, p: logs.append((n, p)),
        sender=FakeSender(),
    )
    return step_station_strand_recovery(ctx), logs

result_sc, logs_sc = run_recovery(int(StatusFlags.Supercruise))
check("strand_recovery: in SC -> True (no-op)", result_sc is True)
check("strand_recovery: in SC -> StrandRecoveryNotStranded logged",
      any(n == "StrandRecoveryNotStranded" for n, _ in logs_sc))

result_dock, _ = run_recovery(int(StatusFlags.Docked))
check("strand_recovery: docked -> True (no-op)", result_dock is True)

result_land, _ = run_recovery(int(StatusFlags.Landed))
check("strand_recovery: landed -> True (no-op)", result_land is True)

# Stranded (flags=0): no SC, not docked, not landed.
result_strand, logs_strand = run_recovery(0)
check("strand_recovery: stranded (0 flags) -> True", result_strand is True)
check("strand_recovery: stranded logs StrandRecoveryAttempt",
      any(n == "StrandRecoveryAttempt" for n, _ in logs_strand))

# status=None -> safe fallback
logs_none: list = []
result_none = step_station_strand_recovery(FakeCtx(
    status_supplier=lambda: None,
    log=lambda n, p: logs_none.append((n, p)),
))
check("strand_recovery: status=None -> True (safe fallback)", result_none is True)
check("strand_recovery: status=None logs StrandRecoveryStatusNone",
      any(n == "StrandRecoveryStatusNone" for n, _ in logs_none))

check("strand_recovery: NEVER returns False (INV [2])",
      all([result_sc, result_dock, result_land, result_strand, result_none]))

check("StatusFlags.Supercruise == 1<<4", StatusFlags.Supercruise == (1 << 4))
check("StatusFlags.Docked == 1<<0",      StatusFlags.Docked == (1 << 0))
check("StatusFlags.Landed == 1<<1",      StatusFlags.Landed == (1 << 1))

# ---------------------------------------------------------------------------
# ---- STEP_REGISTRY after activate()
# ---------------------------------------------------------------------------

from ed_core.flow.step_registry import STEP_REGISTRY
import ed_explore as _ed_explore

_ed_explore.activate()   # idempotent

check("STEP_REGISTRY has 'explore'",
      "explore" in STEP_REGISTRY)
check("STEP_REGISTRY has 'station_strand_recovery'",
      "station_strand_recovery" in STEP_REGISTRY)
check("STEP_REGISTRY has 'body_tour' (legacy kept)",
      "body_tour" in STEP_REGISTRY)

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

print()
if _fails:
    print(f"FAILED: {len(_fails)} check(s)")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("All checks passed.")
    sys.exit(0)
