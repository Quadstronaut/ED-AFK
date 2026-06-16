"""C-series boot scene TEMPLATES — 11 states, determination-only.

Spec: docs/superpowers/specs/2026-06-15-cseries-boot-determination-spec.md (§3).

Each of the 11 C-series states is a SceneTemplate carrying:
  - determine(ctx) -> bool | None : telemetry verdict. None == CV-PENDING
    (honest abstention; never a fabricated CV guess — INV7).
  - proc: str | None              : the live RUN-procedure name this scene maps
    to (mirrors boot_routes._STATE_TO_PROC), or None for idle/fallback/no-
    standalone-proc. INERT DATA — never invoked here (INV1/INV6).
  - fail_closed                    : a named fallback branch (§3 table).

LAYERING: ed_core (rank 1). Imports stdlib + ed_core.boot.primitives only.
NEVER imports a domain, NEVER calls register_* — nothing here is wired into live
dispatch (INV1/INV2). ed_core/__init__.py stays non-eager; this module is
dead-until-imported, and boot/__init__ re-exports primitives ONLY (no scene
side-effect import).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from ed_core.boot.primitives import (
    ArrivalLatch,
    reconstruct_arrival_from_journal,
    fsd_cooldown_blocked,
)


# ---------------------------------------------------------------------------
# State enum (11 states — spec §3)
# ---------------------------------------------------------------------------

class CSeriesState(enum.Enum):
    DOCKED = "DOCKED"
    STARTUP = "STARTUP"
    ARRIVAL = "ARRIVAL"
    REFUEL = "REFUEL"
    TRAVERSAL = "TRAVERSAL"
    EXPLORATION = "EXPLORATION"
    STARSMACK = "STARSMACK"
    NO_ROUTE = "NO_ROUTE"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    PARKED = "PARKED"


# ---------------------------------------------------------------------------
# Determination context — read-only telemetry snapshot
# ---------------------------------------------------------------------------

@dataclass
class DetermineContext:
    """A read-only snapshot of telemetry for one determination pass.

    All fields are PLAIN data the engine already has; determination does NOT
    read files, the network, or the ship. CV-derived facts are deliberately
    ABSENT — a state that needs CV to decide returns None (CV-pending).
    """

    status: Any | None = None             # parsed ed_core Status (or None)
    events: Iterable[Any] = field(default_factory=tuple)  # journal, newest-last
    route_empty: bool = True              # NavRoute.json Route == []
    arrival_latch: ArrivalLatch = field(default_factory=ArrivalLatch)
    exploration_mode: bool = False        # operator/launcher exploration flag
    fsd_cooldown: bool = False            # derived: fsd_cooldown_blocked(status)
    smacked: bool = False                 # last SC drop was at a massive body (Star or Planet)
    paused: bool = False                  # LP4 cooperative pause flag
    diverged: bool = False                # LP4 log/state divergence (resume)

    # Shape (i) — CV-confirmed smack body kind (OQ7 resolved).
    # None = unknown / abstain (CV not wired or not yet read at classify time).
    # 'star' | 'planet' = CV-confirmed via escape-vector color.
    #
    # NOTE: build_determine_context in boot_routes.py has NO frame at classify
    # time, so it leaves this field at its default (None). That means the C-series
    # classify path NEVER enters STARSMACK from bare telemetry — it abstains.
    # The live CV gate lives in _route_sc_exit (the event-route path), which
    # DOES have a frame and sets runner._smack_kind before latch and dispatch.
    # Shape (i), smack_kind unset at classify time -> abstain; the live CV gate
    # lives in _route_sc_exit.
    smack_kind: Optional[str] = None     # None=unknown/abstain, 'star'/'planet'=CV-confirmed

    # --- telemetry accessors (fail-closed; None status -> conservative) ---

    @property
    def docked(self) -> bool:
        return bool(getattr(self.status, "docked", False)) if self.status else False

    @property
    def in_supercruise(self) -> bool:
        return (bool(getattr(self.status, "in_supercruise", False))
                if self.status else False)

    @property
    def scooping_fuel(self) -> bool:
        return (bool(getattr(self.status, "scooping_fuel", False))
                if self.status else False)

    @property
    def low_fuel(self) -> bool:
        return bool(getattr(self.status, "low_fuel", False)) if self.status else False


# ---------------------------------------------------------------------------
# Scene template
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SceneTemplate:
    """One C-series scene: determine + inert proc metadata + named fail-closed branch."""

    state: CSeriesState
    determine: Callable[[DetermineContext], Optional[bool]]
    proc: Optional[str]      # live procedure name (mirrors _STATE_TO_PROC); None = idle/fallback/no-standalone-proc
    fail_closed: str
    gate: str = ""          # human-readable named gate (spec §3)


# ---------------------------------------------------------------------------
# Per-state determination predicates (telemetry-wired; None == CV-pending)
# ---------------------------------------------------------------------------

def _det_docked(ctx: DetermineContext) -> bool:
    # Docked flag (bit 0). No status -> fail-closed to STARTUP, so NOT docked.
    return ctx.docked


def _det_startup(ctx: DetermineContext) -> bool:
    # Fresh boot in normal space with a route to fly: not docked, not in SC,
    # route present, not smacked. (Lost-SC / first-launch align.)
    return (
        ctx.status is not None
        and not ctx.docked
        and not ctx.in_supercruise
        and not ctx.route_empty
        and not ctx.smacked
    )


def _det_arrival(ctx: DetermineContext) -> bool:
    # LP1: a hyperspace arrival (FSDJump) reconstructed from the journal AND
    # currently in supercruise (the arrival scene IS in SC). PIN 2/3 semantics
    # live in reconstruct_arrival_from_journal.
    return (
        ctx.in_supercruise
        and reconstruct_arrival_from_journal(ctx.events)
    )


def _det_refuel(ctx: DetermineContext) -> bool:
    # Actively scooping, OR low-fuel while in SC (scoop-imminent at the arrival
    # star). Conservative OR — see OPEN QUESTION 3.
    return ctx.scooping_fuel or (ctx.low_fuel and ctx.in_supercruise)


def _det_traversal(ctx: DetermineContext) -> bool:
    # Cruising toward the next hop: in SC, route present, arrival NOT latched
    # (a latched arrival routes to ARRIVAL, which is higher priority anyway).
    return (
        ctx.in_supercruise
        and not ctx.route_empty
        and not ctx.arrival_latch.armed
    )


def _det_exploration(ctx: DetermineContext) -> bool:
    """PIN 5 — telemetry-wired, returns a BOOL (never None).

    EXPLORATION iff: in_supercruise AND route empty AND arrival latch NOT armed
    AND exploration_mode. The arrival latch is UNSET in this state (the inverse
    of the prior run's bug, which required armed). The FSS *action* is Phase-2;
    the scene *detection* is telemetry-sufficient.
    """
    return (
        ctx.in_supercruise
        and ctx.route_empty
        and not ctx.arrival_latch.armed
        and ctx.exploration_mode
    )


def _det_starsmack(ctx: DetermineContext) -> Optional[bool]:
    """Enter STARSMACK only when the escape-vector CV has CONFIRMED a smack.

    Shape (i) — CV fact threaded into DetermineContext.smack_kind (OQ7 resolved):
      ctx.smack_kind == 'star' or 'planet'  -> True  (CV-confirmed smack)
      ctx.smack_kind is None AND ctx.smacked -> None  (CV-PENDING abstain, INV7)
      ctx.smack_kind is None AND not ctx.smacked -> False

    INVARIANT (INV1, BUG B fix): a bare SupercruiseExit with no escape-vector
    CV evidence MUST NOT cause smack_recovery — returning None here ensures
    scene_for() treats this as 'not routed here' (honest abstention, INV7).

    At classify time (build_determine_context), smack_kind is ALWAYS None
    because no frame is available — so this NEVER returns True from cold
    telemetry. The live CV gate is _route_sc_exit (the event-route path),
    which acquires a frame, calls detect_escape_vector + classify_smack, and
    sets runner._smack_kind before updating runner._smacked. The C-series
    classify path inherits that confirmed kind on a HOT restart only.

    Game-truth: blue escape vector = star-smack, purple = planet-smack.
    (Operator-witnessed; docs/superpowers/specs/2026-06-16-obstruction-and-smack-game-truth.md)
    """
    if ctx.smack_kind in {"star", "planet"}:
        return True                   # CV-confirmed smack -> enter STARSMACK
    if ctx.smacked:
        return None                   # drop happened but CV has not confirmed -> abstain
    return False


def _det_no_route(ctx: DetermineContext) -> bool:
    # Normal-space, empty route, exploration off, NOT smacked: nothing to fly,
    # never plotted. The `not smacked` guard keeps a smacked ship out of idle
    # (STARSMACK owns it) even if scene priority is ever reordered.
    return (
        ctx.route_empty
        and not ctx.docked
        and not ctx.in_supercruise
        and not ctx.exploration_mode
        and not ctx.smacked
    )


def _det_pause(ctx: DetermineContext) -> bool:
    # LP4 cooperative pause flag set.
    return ctx.paused


def _det_resume(ctx: DetermineContext) -> bool:
    # LP4: unpaused AND log/state divergence -> re-derive the scene next tick.
    return (not ctx.paused) and ctx.diverged


def _det_parked(ctx: DetermineContext) -> bool:
    # Terminal idle: empty route, not in SC, not docked, no divergence, NOT
    # smacked (a smacked ship belongs in STARSMACK, never parked idle).
    return (
        ctx.route_empty
        and not ctx.in_supercruise
        and not ctx.docked
        and not ctx.diverged
        and not ctx.smacked
    )


# ---------------------------------------------------------------------------
# The 11 templates, in PRIORITY order (highest first — spec §3)
# ---------------------------------------------------------------------------

C_SERIES_SCENES: tuple[SceneTemplate, ...] = (
    SceneTemplate(
        state=CSeriesState.PAUSE,
        determine=_det_pause,
        proc=None,                # routes to legacy classifier; no standalone proc
        gate="LP4 pause flag set",
        fail_closed="PARKED (cannot resume)",
    ),
    SceneTemplate(
        state=CSeriesState.RESUME,
        determine=_det_resume,
        proc=None,                # OPEN follow-on — re-derive has NO standalone live procedure; proc=None until built
        gate="unpause + log/state divergence (LP4)",
        fail_closed="STARTUP (re-derive from scratch)",
    ),
    SceneTemplate(
        state=CSeriesState.STARSMACK,
        determine=_det_starsmack,
        proc="smack_recovery",
        gate="escape-vector CV confirmed smack (blue=star / purple=planet); ABSTAINS on bare telemetry",
        fail_closed="ARRIVAL (escape-vector CV unwired or negative -> benign drop; INV1 fail-closed)",
    ),
    SceneTemplate(
        state=CSeriesState.ARRIVAL,
        determine=_det_arrival,
        proc="arrival",
        gate="FSDJump arrival latched (LP1) + in SC",
        fail_closed="TRAVERSAL (no arrival evidence)",
    ),
    SceneTemplate(
        state=CSeriesState.REFUEL,
        determine=_det_refuel,
        proc=None,                # routes to legacy classifier; no standalone proc
        gate="ScoopingFuel flag / LowFuel in SC",
        fail_closed="ARRIVAL (scoop window is part of arrival)",
    ),
    SceneTemplate(
        state=CSeriesState.DOCKED,
        determine=_det_docked,
        proc=None,                # idle, no live proc
        gate="Docked flag (bit 0)",
        fail_closed="STARTUP (no status -> don't assume docked)",
    ),
    SceneTemplate(
        state=CSeriesState.TRAVERSAL,
        determine=_det_traversal,
        proc=None,                # routes to legacy classifier; no standalone proc
        gate="SC flag + route present + arrival not latched",
        fail_closed="STARTUP (lost supercruise)",
    ),
    SceneTemplate(
        state=CSeriesState.EXPLORATION,
        determine=_det_exploration,
        proc=None,                # OPEN follow-on — honk/FSS/body-tour has NO standalone live procedure yet (Council C bucket B/C); proc=None until built
        gate="SC + empty route + no latch + exploration_mode (PIN 5)",
        fail_closed="PARKED (mode off)",
    ),
    SceneTemplate(
        state=CSeriesState.STARTUP,
        determine=_det_startup,
        proc="startup",
        gate="first status seen, route non-empty, normal space",
        fail_closed="NO_ROUTE (route absent)",
    ),
    SceneTemplate(
        state=CSeriesState.NO_ROUTE,
        determine=_det_no_route,
        proc=None,                # idle; side-effect label lives in boot_routes
        gate="empty route, normal space, exploration off",
        fail_closed="PARKED (terminal idle)",
    ),
    SceneTemplate(
        state=CSeriesState.PARKED,
        determine=_det_parked,
        proc=None,                # idle; side-effect label lives in boot_routes
        gate="empty route, not SC, not docked, no divergence",
        fail_closed="(terminal; no further branch)",
    ),
)

# Invariant guard: exactly 11, one per state, priority-ordered (asserted at
# import so a builder editing the tuple can't silently drop/dupe a state).
assert len(C_SERIES_SCENES) == 11, "C_SERIES_SCENES must hold exactly 11 states"
assert {t.state for t in C_SERIES_SCENES} == set(CSeriesState), \
    "C_SERIES_SCENES must cover every CSeriesState exactly once"


# ---------------------------------------------------------------------------
# Scene selection
# ---------------------------------------------------------------------------

def scene_for(ctx: DetermineContext) -> Optional[SceneTemplate]:
    """Return the highest-priority scene whose determine(ctx) is TRUTHY.

    Walks C_SERIES_SCENES in priority order; the first template whose
    determine() returns True wins. A None (CV-PENDING) verdict is treated as
    "not routed here" — honest abstention, never a guess (INV7) — so a scene
    that cannot decide from telemetry does NOT hijack routing. Returns None when
    NOTHING matches (the engine then holds / re-derives next tick).

    NOTE: determination ONLY; the caller maps tmpl.state via the live _STATE_TO_PROC.
    No action body exists here.
    """
    for tmpl in C_SERIES_SCENES:
        if tmpl.determine(ctx) is True:
            return tmpl
    return None


def scene_by_state(state: CSeriesState) -> Optional[SceneTemplate]:
    """Look up a template by its state (diagnostics / tests)."""
    for tmpl in C_SERIES_SCENES:
        if tmpl.state is state:
            return tmpl
    return None


__all__ = [
    "CSeriesState",
    "DetermineContext",
    "SceneTemplate",
    "C_SERIES_SCENES",
    "scene_for",
    "scene_by_state",
]
