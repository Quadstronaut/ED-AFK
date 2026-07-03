"""ed-explore — exploration domain for the ED-AFK bot.

Phase 1: the relocated body-tour step, registered into the core step table and
wired into arrival.toml exactly as today. Depends on ed-core + ed-vision; never
imports a sibling domain. (Phase-1 reorg skeleton; step relocates here in
Step 5.)
"""

from __future__ import annotations

__version__ = "0.2.0"

_activated = False


def activate() -> None:
    """Register ed-explore's contributions into the core registry surfaces.

    Imports steps_body_tour as a side effect, which registers `body_tour` into
    the merged core step table (surface #3). Called by the CLI host active-set
    registrar before constructing FlowRunner. Idempotent.
    """
    global _activated
    if _activated:
        return
    _activated = True
    from . import steps_body_tour as _bt  # noqa: F401 — registers body_tour step
    # steps_explore (`explore`) + steps_strand_recovery (`station_strand_recovery`)
    # REMOVED 2026-06-27 (flow-redesign): the arrival `explore`/strand steps are
    # superseded by the exploration.toml scene (nav_supercruise_unexplored LOOP);
    # the unexplored tour ORBITS (no strand-drop), so no strand recovery is needed
    # (C6-UNEXPLORED-ORBIT-FINDING). Both step modules were deleted; activate()
    # now registers only body_tour.
