"""C2 section-transition orchestrator — acceptance suite (PURE).

Imports the REAL flight-code symbols from ed_autojump.flow.boot_routes and
ed_core.flow.predicates (NOT a reference copy). No game / CV / network. Status
and NavRoute fixtures are built with the REAL parsers (parse_status /
parse_navroute) so the discriminators run over real pydantic models, not
hand-rolled stand-ins.

Ports the C2 design council's 29 tests MINUS the two _exploration_mode tests
(deviation §7 — LOCKED #9 reads _body_tour_enabled, not the phantom) PLUS the
abort-recheck Groups B/C and the exploration-source Group E.

Maps onto the spec's invariants:
  Group A — transition_to fail-closed                 INV-1, INV-3
  Group B — abort-recheck at point (b) in transition_to  INV-3, INV-4
  Group C — abort-recheck at point (a) in run_arrival_then_branch  INV-2, INV-4
  Group D — arrival branch table + precedence         INV-6
  Group E — exploration source per LOCKED #9          INV-5
  Group F — _dest_is_system over real parsers         INV-7
  Group G — D1 isolation                              INV-8
  Group H — totality + external signature             INV-11, INV-12
"""

from __future__ import annotations

import inspect

from ed_core.status.status import parse_status
from ed_core.status.navroute import parse_navroute

# REAL flight-code symbols (the BUILD deliverable — never a copy).
from ed_autojump.flow.boot_routes import (
    _SECTION_TO_PROC,
    transition_to,
    run_arrival_then_branch,
    _transition_aborted,
    _arrival_branch,
    _dest_is_system,
    _dest_is_station,
    _exploration_active,
)
import ed_autojump.flow.boot_routes as _br


# ---------------------------------------------------------------------------
# Fixtures over the REAL parsers
# ---------------------------------------------------------------------------

SYS = "Acihaut"
SYS_ADDR = 358797513434


def _status(*, dest_name=None, dest_body=0, dest_system=0,
            in_supercruise=True):
    """Build a real Status via parse_status. A Destination block is emitted
    only when one of its fields is set (mirrors the sparse Status.json)."""
    obj: dict = {"Flags": (1 << 4) if in_supercruise else 0}
    if dest_name is not None or dest_body or dest_system:
        obj["Destination"] = {
            "System": dest_system,
            "Body": dest_body,
            "Name": dest_name or "",
        }
    return parse_status(obj)


def _waypoint(name: str, addr: int):
    return {
        "StarSystem": name,
        "SystemAddress": addr,
        "StarPos": [0.0, 0.0, 0.0],
        "StarClass": "G",
    }


def _route(*names_and_addrs):
    """Build a real NavRoute via parse_navroute from (name, addr) pairs.
    No args -> the empty (cleared) route."""
    return parse_navroute(
        {"Route": [_waypoint(n, a) for (n, a) in names_and_addrs]})


def _empty_route():
    return parse_navroute({"Route": []})


# ---------------------------------------------------------------------------
# Pure runner stand-in (records _run dispatches; mirrors FakeSender discipline)
# ---------------------------------------------------------------------------

class _FakeNavReader:
    """Mirrors NavRouteReader: poll() yields the value once then None
    (mtime-unchanged), .current always holds it — exactly how
    _navroute_state falls back. `value` may be a NavRoute or None."""

    def __init__(self, value):
        self._value = value
        self._polled = False

    def poll(self):
        if self._polled:
            return None
        self._polled = True
        return self._value

    @property
    def current(self):
        return self._value


class _Runner:
    """Minimal FlowRunner stand-in for the orchestrator. Records every
    _run(name) into `dispatched`, carries the telemetry the orchestrator
    reads, and — load-bearing for the point-(b) ordering test — mirrors the
    REAL dispatcher's `_run`, which CLEARS self._preempt on entry
    (dispatcher.py:514). That lets a test prove the abort-recheck fires
    BEFORE _run would have wiped the preempt flag."""

    def __init__(self, *, status=None, navroute=None, system=SYS,
                 procedures=None, body_tour_enabled=False,
                 smacked=False, preempt=None, running_proc=None,
                 should_abort=False, exploration_mode=None):
        self.dispatched: list[str] = []
        self._latest_status = status if status is not None else _status()
        self._navroute_reader = _FakeNavReader(navroute)
        self._current_system = system
        # Default procedure set: every section proc loaded + arrival.
        if procedures is None:
            procedures = {"arrival": object(), "dock": object(),
                          "exploration": object(), "traversal": object()}
        self.procedures = procedures
        self._body_tour_enabled = body_tour_enabled
        self._smacked = smacked
        self._preempt = preempt
        self._running_proc = running_proc
        self._abort = should_abort
        # The PHANTOM. Set ONLY in the Group-E proof that it is never read.
        # Absent by default (matches the real runner, where it is unassigned).
        if exploration_mode is not None:
            self._exploration_mode = exploration_mode

    def _navroute_state(self):
        r = self._navroute_reader
        nr = r.poll()
        return nr if nr is not None else r.current

    def _should_abort(self) -> bool:
        return self._abort

    def _run(self, name: str) -> None:
        # Mirror dispatcher.py:514 — a real _run clears _preempt on entry.
        # If the abort-recheck (point b) ran AFTER this, a set preempt would
        # already be gone and dispatch would wrongly proceed; the ordering
        # test relies on this fidelity.
        self._preempt = None
        self._running_proc = name
        self.dispatched.append(name)


# ===========================================================================
# Group A — transition_to fail-closed (INV-1, INV-3)
# ===========================================================================

def test_transition_dispatches_section_proc():
    r = _Runner()
    assert transition_to(r, "traversal") == "traversal"
    assert r.dispatched == ["traversal"]


def test_transition_docking_maps_to_dock_proc():
    r = _Runner()
    assert transition_to(r, "docking") == "dock"
    assert r.dispatched == ["dock"]


def test_transition_unknown_section_returns_empty_no_dispatch():
    r = _Runner()
    assert transition_to(r, "no_such_section") == ""
    assert r.dispatched == []


def test_transition_missing_proc_fail_closed():
    # Section known, but its procedure is not loaded -> "" , no dispatch.
    r = _Runner(procedures={"arrival": object()})  # no "traversal" proc
    assert transition_to(r, "traversal") == ""
    assert r.dispatched == []


# ===========================================================================
# Group B — abort-recheck at point (b), inside transition_to (INV-3, INV-4)
# ===========================================================================

def test_transition_suppressed_when_smacked():
    r = _Runner(smacked=True)
    assert transition_to(r, "traversal") == ""
    assert r.dispatched == []


def test_transition_suppressed_when_preempt_set():
    r = _Runner(preempt="star_smack")
    assert transition_to(r, "traversal") == ""
    assert r.dispatched == []


def test_transition_suppressed_when_should_abort():
    r = _Runner(should_abort=True)
    assert transition_to(r, "traversal") == ""
    assert r.dispatched == []


def test_transition_recheck_precedes_run_clearing_preempt():
    # Ordering proof (INV-3): the abort-recheck reads _preempt BEFORE
    # runner._run (which would clear it). With _preempt set, transition_to
    # must return "" and dispatch NOTHING. If the recheck ran after _run, the
    # preempt would already be cleared and "traversal" would dispatch — the
    # empty dispatched list proves the order.
    r = _Runner(preempt="star_smack")
    assert transition_to(r, "traversal") == ""
    assert r.dispatched == []
    # _preempt is untouched by the suppressed path (no _run ran to clear it).
    assert r._preempt == "star_smack"


# ===========================================================================
# Group C — run_arrival_then_branch abort-recheck at point (a) (INV-2, INV-4)
# ===========================================================================

class _SmackOnArrivalRunner(_Runner):
    """A runner whose arrival scene LANDS A SMACK: _run('arrival') sets the
    smack/preempt flags (as the real preempt path would, dispatcher.py:621-643
    — _running_proc 'arrival' is in _PREEMPT_ON_SMACK). Proves the point-(a)
    recheck reads the flags arrival left and suppresses the branch."""

    def _run(self, name: str) -> None:
        super()._run(name)
        if name == "arrival":
            self._smacked = True
            self._preempt = "star_smack"


def test_branch_suppressed_after_smack_landing():
    r = _SmackOnArrivalRunner(navroute=_route((SYS, SYS_ADDR),
                                              ("Next", 99)))
    assert run_arrival_then_branch(r) == "arrival"
    assert r.dispatched == ["arrival"]          # NO section proc


def test_branch_suppressed_after_preempt():
    # _preempt set post-arrival (no smack flag), onward route present.
    class _R(_Runner):
        def _run(self, name):
            super()._run(name)
            if name == "arrival":
                self._preempt = "star_smack"

    r = _R(navroute=_route((SYS, SYS_ADDR), ("Next", 99)))
    assert run_arrival_then_branch(r) == "arrival"
    assert r.dispatched == ["arrival"]


def test_branch_suppressed_on_operator_abort():
    class _R(_Runner):
        def _run(self, name):
            super()._run(name)
            if name == "arrival":
                self._abort = True

    r = _R(navroute=_route((SYS, SYS_ADDR), ("Next", 99)))
    assert run_arrival_then_branch(r) == "arrival"
    assert r.dispatched == ["arrival"]


def test_smack_mid_arrival_does_not_branch_into_exclusion_zone():
    # AC7 / INV-4 — the BLOCKER test. _running_proc left at 'arrival', a smack
    # applied (_smacked=True, _preempt='star_smack'): NO section proc runs,
    # only ['arrival']; the bot can never branch into the exclusion zone.
    r = _SmackOnArrivalRunner(
        navroute=_route((SYS, SYS_ADDR), ("Next", 99)))
    run_arrival_then_branch(r)
    assert r.dispatched == ["arrival"]
    for forbidden in ("dock", "traversal", "exploration"):
        assert forbidden not in r.dispatched
    # The runner is left flagged so run_live -> _route_sc_exit owns recovery.
    assert r._smacked is True
    assert r._running_proc == "arrival"


def test_clean_arrival_branches():
    # No abort flags, an onward route -> arrival THEN traversal.
    r = _Runner(navroute=_route((SYS, SYS_ADDR), ("Next", 99)))
    assert run_arrival_then_branch(r) == "arrival"
    assert r.dispatched == ["arrival", "traversal"]


# ===========================================================================
# Group C2 — Exploration is a TERMINAL branch (G2, operator 2026-07-11 —
# REPEALS the C2-D5 unconditional exploration->traversal chain). The operator's
# 2026-07-07 toml reorg (70c248e) gave exploration.toml its own terminal jump
# tail; chaining traversal after it pressed the SAME jump-tail keys a second
# time, possibly mid-hyperspace-tunnel.
# ===========================================================================

class _SmackOnExplorationRunner(_Runner):
    """A runner whose EXPLORATION tour lands a smack: _run('exploration') sets
    the smack/preempt flags. The tour must still yield to _route_sc_exit
    (never the exclusion zone) — unchanged by the G2 chain removal."""

    def _run(self, name: str) -> None:
        super()._run(name)
        if name == "exploration":
            self._smacked = True
            self._preempt = "star_smack"


def test_clean_arrival_exploration_on_is_terminal_no_traversal_chain():
    # Onward hop + body_tour_enabled=True -> arrival, exploration. NOTHING
    # after: exploration.toml owns its own jump tail (G2 — the old chained
    # traversal double-pressed the jump keys).
    r = _Runner(
        status=_status(dest_name="Next", dest_body=0, dest_system=99),
        navroute=_route((SYS, SYS_ADDR), ("Next", 99)),
        body_tour_enabled=True)
    assert run_arrival_then_branch(r) == "arrival"
    assert r.dispatched == ["arrival", "exploration"]


def test_clean_arrival_exploration_off_dispatches_arrival_traversal():
    # Onward hop + body_tour_enabled=False -> arrival, traversal (NO exploration).
    r = _Runner(
        status=_status(dest_name="Next", dest_body=0, dest_system=99),
        navroute=_route((SYS, SYS_ADDR), ("Next", 99)),
        body_tour_enabled=False)
    assert run_arrival_then_branch(r) == "arrival"
    assert r.dispatched == ["arrival", "traversal"]


def test_empty_route_dispatches_arrival_then_dock():
    # Empty route -> terminal -> docking. Docking precedence unchanged; the
    # exploration chain does NOT fire (dock is a terminal branch, not exploration).
    r = _Runner(navroute=_empty_route())
    assert run_arrival_then_branch(r) == "arrival"
    assert r.dispatched == ["arrival", "dock"]


def test_smack_mid_exploration_leaves_runner_flagged_for_sc_exit():
    # A smack landing DURING the exploration tour: only [arrival, exploration]
    # dispatched, runner left flagged for _route_sc_exit. (Trivially no chain
    # since G2, but the smack flags must survive the branch untouched.)
    r = _SmackOnExplorationRunner(
        status=_status(dest_name="Next", dest_body=0, dest_system=99),
        navroute=_route((SYS, SYS_ADDR), ("Next", 99)),
        body_tour_enabled=True)
    assert run_arrival_then_branch(r) == "arrival"
    assert r.dispatched == ["arrival", "exploration"]
    assert "traversal" not in r.dispatched
    assert r._smacked is True


# ===========================================================================
# Group D — arrival branch table + precedence (INV-6)
# ===========================================================================

def test_arrived_no_route_goes_docking():
    # Empty route -> terminal -> docking.
    r = _Runner(navroute=_empty_route())
    assert _arrival_branch(r) == "docking"


def test_local_star_dest_with_onward_route_goes_traversal():
    # LIVE REFUTATION 2026-07-06 (run 095532, finding 13): this exact shape —
    # onward route present, Destination = the local primary star — is the
    # NORMAL state after EVERY clean arrival (post-jump residual lock; and
    # arrival's own SC-assist locks the local star by design). The old
    # corroborant branched it "docking" with 17 jumps remaining. A non-empty
    # route is an onward hop, PERIOD -> traversal.
    r = _Runner(
        status=_status(dest_name=SYS, dest_body=0, dest_system=SYS_ADDR),
        navroute=_route((SYS, SYS_ADDR), ("Next", 99)))
    assert _arrival_branch(r) == "traversal"


def test_onward_hop_exploration_off_goes_traversal():
    r = _Runner(
        status=_status(dest_name="Next", dest_body=0, dest_system=99),
        navroute=_route((SYS, SYS_ADDR), ("Next", 99)),
        body_tour_enabled=False)
    assert _arrival_branch(r) == "traversal"


def test_onward_hop_exploration_on_goes_exploration():
    r = _Runner(
        status=_status(dest_name="Next", dest_body=0, dest_system=99),
        navroute=_route((SYS, SYS_ADDR), ("Next", 99)),
        body_tour_enabled=True)
    assert _arrival_branch(r) == "exploration"


def test_precedence_docking_beats_exploration():
    # dest_is_system FIRST: arrived-at-destination with exploration ON still
    # branches docking (empty route makes dest_is_system True).
    r = _Runner(navroute=_empty_route(), body_tour_enabled=True)
    assert _arrival_branch(r) == "docking"


def test_station_dest_with_onward_route_not_docking_yet():
    # An onward-hop with a station-flavoured (Body!=0) dest is NOT the local
    # star, so dest_is_system is False -> branch is traversal, NOT docking.
    r = _Runner(
        status=_status(dest_name="Jameson Memorial", dest_body=7,
                       dest_system=99),
        navroute=_route((SYS, SYS_ADDR), ("Next", 99)),
        body_tour_enabled=False)
    assert _arrival_branch(r) == "traversal"


# ===========================================================================
# Group E — exploration source per LOCKED #9 (INV-5) — REPLACES the
# design council's two _exploration_mode tests (deviation §7)
# ===========================================================================

def test_exploration_active_reads_body_tour_enabled_true():
    r = _Runner(body_tour_enabled=True)
    assert _exploration_active(r) is True


def test_exploration_active_false_when_body_tour_disabled():
    r = _Runner(body_tour_enabled=False)
    assert _exploration_active(r) is False


def test_exploration_active_ignores_phantom_exploration_mode():
    # The phantom is True, the REAL flag is False -> exploration is OFF.
    # Proves _exploration_active does NOT read _exploration_mode.
    r = _Runner(body_tour_enabled=False, exploration_mode=True)
    assert _exploration_active(r) is False


def test_exploration_active_fail_closed_when_flag_unset():
    # Neither flag present at all -> fail-closed False (getattr default).
    class _Bare:
        pass
    assert _exploration_active(_Bare()) is False


def test_branch_uses_body_tour_for_exploration():
    r = _Runner(
        status=_status(dest_name="Next", dest_body=0, dest_system=99),
        navroute=_route((SYS, SYS_ADDR), ("Next", 99)),
        body_tour_enabled=True)
    assert _arrival_branch(r) == "exploration"


def test_source_grep_new_predicate_does_not_read_phantom():
    # AC2 belt-and-suspenders: the _exploration_active SOURCE reads
    # _body_tour_enabled and NOT _exploration_mode. (A docstring may NAME the
    # phantom to explain the deviation; the executable body must not read it.)
    # The compiled code object is the ground truth. getattr(runner,
    # "_body_tour_enabled", False) loads the attribute name as a STRING
    # CONSTANT (co_consts), not a co_name — so scan both tables. The phantom
    # _exploration_mode appears in NEITHER (a docstring mention never compiles
    # into the bytecode).
    co = _exploration_active.__code__
    referenced = set(co.co_names) | {c for c in co.co_consts
                                     if isinstance(c, str)}
    assert "_body_tour_enabled" in referenced
    assert "_exploration_mode" not in referenced


# ===========================================================================
# 2026-06-21 code council (run wf_ccff331d-506) — the 3 exploration-determination
# fixes: FIX-1 phantom->body_tour at build_determine_context; FIX-2 EXPLORATION
# stays ("fallback", None); FIX-3 capture-at-plot local-star guard.
# ===========================================================================

def test_build_determine_context_reads_body_tour_not_phantom():
    # FIX-1 (D-PHANTOM): the C-series determination GATE must read the SAME
    # real flag _exploration_active does, NOT the never-set _exploration_mode
    # phantom (which kept the EXPLORATION scene unreachable). Bytecode is
    # ground truth (mirrors the helper test above).
    from ed_autojump.flow.boot_routes import build_determine_context
    co = build_determine_context.__code__
    referenced = set(co.co_names) | {c for c in co.co_consts
                                     if isinstance(c, str)}
    assert "_body_tour_enabled" in referenced
    assert "_exploration_mode" not in referenced


def test_state_to_proc_exploration_is_fallback_and_map_total():
    # FIX-2 (D-MAPPING): EXPLORATION deliberately stays ("fallback", None) —
    # no standalone exploration proc exists, so routing it would dispatch a
    # non-existent proc (ship-unsafe). The map must stay total over CSeriesState.
    from ed_autojump.flow.boot_routes import _STATE_TO_PROC
    from ed_core.boot.scenes import CSeriesState
    assert _STATE_TO_PROC[CSeriesState.EXPLORATION] == ("fallback", None)
    assert set(_STATE_TO_PROC) == set(CSeriesState)


def test_captured_name_is_local_star_guard():
    # FIX-3 (D-GUARD): the capture-at-plot fast path must exclude a locked
    # arrival STAR (Body!=0, Name==system) from station classification, using
    # the SAME naming rule the settle loop uses, and FAIL CLOSED (treat as
    # star -> park) on any unprovable/empty input.
    from ed_autojump.flow.boot_routes import _captured_name_is_local_star as g
    assert g("Robigo", "Robigo") is True          # primary star = bare system
    assert g("Robigo A", "Robigo") is True         # secondary "<system> X"
    assert g("Robigo Mines", "Robigo") is False    # a real station -> may dock
    assert g("Robigo AB", "Robigo") is False       # multi-char suffix != secondary
    assert g(None, "Robigo") is True               # fail-closed: missing name
    assert g("", "Robigo") is True                 # fail-closed: empty name
    assert g("Robigo", None) is True               # fail-closed: missing system
    assert g("Robigo", "") is True                 # fail-closed: empty system


def test_dispatch_route_complete_wires_the_local_star_guard():
    # FIX-3 wiring: the guard must actually be CALLED in the fast path, not
    # merely defined. Source-level proof; the behavioural dock path is covered
    # by test_dock.py's existing captured-station test (verified unregressed).
    import inspect as _inspect
    from ed_autojump.flow.boot_routes import dispatch_route_complete
    assert "_captured_name_is_local_star" in _inspect.getsource(dispatch_route_complete)


# ===========================================================================
# Group F — _dest_is_system over the REAL parsers (INV-7)
# ===========================================================================

def test_dest_is_system_primary_empty_route():
    # Empty route is the PRIMARY signal -> True regardless of status.
    assert _dest_is_system(_status(), [], SYS) is True


def test_dest_is_system_none_route_is_terminal():
    # None route also -> True (fail-closed terminal).
    assert _dest_is_system(_status(), None, SYS) is True


def test_dest_is_system_local_star_dest_still_onward():
    # LIVE REFUTATION 2026-07-06 (finding 13): non-empty route + dest = the
    # local primary star is EVERY clean post-jump arrival's state — the old
    # corroborant read it as 'arrived' and dispatched dock 17 jumps early.
    # Non-empty route -> False, no corroborant override.
    st = _status(dest_name=SYS, dest_body=0, dest_system=SYS_ADDR)
    route = _route((SYS, SYS_ADDR), ("Next", 99)).route
    assert _dest_is_system(st, route, SYS) is False


def test_dest_is_system_false_next_hop_star():
    # Non-empty route, dest is the NEXT hop's star (not local) -> False.
    st = _status(dest_name="Next", dest_body=0, dest_system=99)
    route = _route((SYS, SYS_ADDR), ("Next", 99)).route
    assert _dest_is_system(st, route, SYS) is False


def test_dest_is_system_false_route_to_station():
    # Non-empty route, dest is a station body (Body!=0) -> not local star
    # -> False (no false 'arrived').
    st = _status(dest_name="Jameson Memorial", dest_body=7, dest_system=99)
    route = _route((SYS, SYS_ADDR), ("Next", 99)).route
    assert _dest_is_system(st, route, SYS) is False


def test_dest_is_system_unknown_system_non_empty_route_false():
    # Non-empty route, system unknown (None) -> _destination_is_local_star
    # returns None -> `is True` is False -> not arrived (fail-closed).
    st = _status(dest_name=SYS, dest_body=0, dest_system=SYS_ADDR)
    route = _route((SYS, SYS_ADDR), ("Next", 99)).route
    assert _dest_is_system(st, route, None) is False


def test_dest_is_system_unjudgeable_dest_false():
    # Non-empty route, dest locked on a $-symbol beacon -> local_star False
    # -> not arrived.
    st = _status(dest_name="$BeaconRow;", dest_body=0, dest_system=SYS_ADDR)
    route = _route((SYS, SYS_ADDR), ("Next", 99)).route
    assert _dest_is_system(st, route, SYS) is False


# ===========================================================================
# Group G — D1 isolation (INV-8)
# ===========================================================================

def test_dest_is_station_true_for_named_body():
    st = _status(dest_name="Jameson Memorial", dest_body=7, dest_system=99)
    assert _dest_is_station(st) is True


def test_dest_is_station_false_for_star_hop():
    # Body == 0 -> an FSD route hop / star, not a station.
    st = _status(dest_name="Next", dest_body=0, dest_system=99)
    assert _dest_is_station(st) is False


def test_dest_is_station_false_for_symbolic_beacon():
    # $-symbol name -> beacon/scenario row, not a station (fails closed).
    st = _status(dest_name="$BeaconRow;", dest_body=3, dest_system=99)
    assert _dest_is_station(st) is False


def test_dest_is_station_fail_closed_on_none():
    # No status / no destination -> False (never a blind station drive).
    assert _dest_is_station(_status()) is False
    assert _dest_is_station(None) is False


def test_dest_is_station_carries_d1_marker():
    # The seam guard: the literal BLOCKED-ON-D1 marker lives inside the
    # _dest_is_station definition in FLIGHT CODE.
    src = inspect.getsource(_dest_is_station)
    assert "BLOCKED-ON-D1" in src


def test_d1_schema_not_hardcoded_as_confirmed_outside_marker():
    # No `Body != 0` station-schema is asserted as CONFIRMED outside the
    # marked predicate. The orchestrator's own source must not re-encode the
    # unverified mechanic; it delegates to the settled READ. (We assert the
    # marker is the SOLE carrier of the D1 seam in boot_routes.)
    mod_src = inspect.getsource(_br)
    assert mod_src.count("BLOCKED-ON-D1") == 1


# ===========================================================================
# Group H — totality + external signature (INV-11, INV-12)
# ===========================================================================

def test_section_map_total():
    assert set(_SECTION_TO_PROC) == {"docking", "exploration", "traversal"}
    assert _SECTION_TO_PROC == {
        "docking": "dock",
        "exploration": "exploration",
        "traversal": "traversal",
    }


def test_arrival_wrapper_returns_arrival_signal_even_when_branched():
    # INV-12 / AC11: run_arrival_then_branch returns the external 'arrival'
    # signal on a clean run that ALSO branched onward.
    r = _Runner(navroute=_route((SYS, SYS_ADDR), ("Next", 99)))
    assert run_arrival_then_branch(r) == "arrival"
    assert r.dispatched == ["arrival", "traversal"]


def test_arrival_wrapper_returns_arrival_signal_when_suppressed():
    # And still 'arrival' when the branch was suppressed by an abort.
    r = _Runner(navroute=_route((SYS, SYS_ADDR), ("Next", 99)),
                smacked=True)
    # _Runner._run doesn't auto-smack, so seed the flag post-construction via
    # a subclass that smacks on arrival to exercise the (a) recheck.
    assert run_arrival_then_branch(r) == "arrival"
    # _smacked was True from construction, so even arrival's recheck (which
    # reads the pre-set flag) suppresses the branch.
    assert r.dispatched == ["arrival"]


# ---------------------------------------------------------------------------
# _transition_aborted unit coverage (the LOCKED #10 core, all three sources)
# ---------------------------------------------------------------------------

def test_transition_aborted_clean_runner_false():
    assert _transition_aborted(_Runner()) is False


def test_transition_aborted_true_on_smacked():
    assert _transition_aborted(_Runner(smacked=True)) is True


def test_transition_aborted_true_on_preempt():
    assert _transition_aborted(_Runner(preempt="star_smack")) is True


def test_transition_aborted_true_on_should_abort():
    assert _transition_aborted(_Runner(should_abort=True)) is True


def test_transition_aborted_missing_attrs_degrade_to_false():
    # A bare object (no flags, no _should_abort) -> not aborted (getattr
    # guards), so a partial stand-in never raises.
    class _Bare:
        pass
    assert _transition_aborted(_Bare()) is False
