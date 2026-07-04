"""Typed, immutable representation of a procedure and its steps."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class Step:
    """One action in a procedure. `params` is everything from the TOML inline
    table except `action` and `required`."""
    action: str
    params: dict[str, Any] = field(default_factory=dict)
    required: bool = False
    # A required failure AT OR AFTER this step resumes HERE instead of at
    # on_required_fail.retry_from (operator rule, 2026-06-07 startup redesign:
    # "if it makes it to 13, failures after that should return to 13").
    retry_anchor: bool = False
    # Forward-skip target for a NON-required step (council-ratified
    # conditional-orbit fix, 2026-06-07, lock-speed redesign): when this step
    # returns False, jump the lane FORWARD to the named action instead of
    # advancing one step. arrival's nav_panel_target uses it so a star NOT
    # found in the bounded scan (far -> obstruction negligible) vaults the
    # whole get-around block (sc_assist_orbit + its wait) and lands on
    # target_next_route. Ignored for required steps and when None.
    skip_to: Optional[str] = None
    # ON-SUCCESS BACK-EDGE for the bounded loop primitive (council #4, the
    # exploration LOOP). The on-True DUAL of skip_to's on-False forward hop: when
    # this step SUCCEEDS (ok=True), the lane jumps BACK to the named action
    # instead of advancing one step, so a scene can repeat a body of steps until
    # a loop-head returns False (its skip_to exit) or the budget is spent.
    # exploration.toml's set_throttle back-edge uses loop_to="nav_supercruise_
    # unexplored" to re-drive the next-unexplored-body pick. BOUNDED by loop_max
    # (below): once the back-edge has fired loop_max times the interpreter logs
    # LoopBudgetExceeded and falls through (exits the loop) so a non-terminating
    # loop can NEVER hang the flight. Ignored when None.
    loop_to: Optional[str] = None
    # Max times a single loop_to back-edge may fire before the budget is spent
    # (fail-closed loop cap). Only meaningful when loop_to is set.
    loop_max: int = 64


@dataclass(frozen=True)
class OnRequiredFail:
    """What to do when a `required` step fails. Default = abort immediately."""
    retry_from: Optional[str] = None   # action name to resume from
    max_retries: int = 0
    backoff_s: float = 0.0
    # State-aware retry override (operator-dictated, 2026-06-07 14:24-14:29Z burn):
    # a PRE-anchor required fail that lands the ship ALREADY in supercruise must
    # NOT restart the real-space ladder (target_ahead deselect + the in-SC
    # engage guard make every real-space orient find nothing — 3 retries burned
    # all-zero). When set AND status reads in_supercruise, resume from this
    # action instead of retry_from. None = no override (arrival/startup), and the
    # interpreter must never consult status in that case.
    retry_from_if_supercruise: Optional[str] = None


@dataclass(frozen=True)
class Procedure:
    name: str
    steps: tuple[Step, ...]
    parallel: bool = False                 # this procedure is a background track
    # NOTE (v1): stop_on_event / timeout_s are RESERVED metadata — parsed and
    # carried, but NOT yet enforced by the interpreter/dispatcher. v1 honk is a
    # self-terminating fixed-length key hold, so its track ends naturally; the
    # dispatcher joins parallel tracks on a fixed 15s cap (see dispatcher._run).
    # A future parallel track that needs early exit on a journal match or its
    # own timeout must add that enforcement before relying on these fields.
    stop_on_event: Optional[str] = None    # (reserved) journal event meant to end a parallel track
    timeout_s: float = 0.0                 # (reserved) intended hard cap for a parallel track (0 = none)
    parallel_tracks: tuple[str, ...] = ()  # procedures to launch concurrently at start
    on_required_fail: OnRequiredFail = field(default_factory=OnRequiredFail)

    def index_of_action(self, action: str) -> Optional[int]:
        """Index of the FIRST step whose action == `action`, else None."""
        for i, s in enumerate(self.steps):
            if s.action == action:
                return i
        return None

    def anchor_at_or_before(self, i: int) -> Optional[int]:
        """Index of the NEAREST retry_anchor step at or before step `i`, else
        None. An anchor later in the procedure never catches earlier failures."""
        for j in range(min(i, len(self.steps) - 1), -1, -1):
            if self.steps[j].retry_anchor:
                return j
        return None
