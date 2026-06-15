"""ed-explore — exploration domain for the ED-AFK bot.

Phase 1: the relocated body-tour step, registered into the core step table and
wired into arrival.toml exactly as today. Depends on ed-core + ed-vision; never
imports a sibling domain. (Phase-1 reorg skeleton; step relocates here in
Step 5.)
"""

from __future__ import annotations

__version__ = "0.2.0"


def activate() -> None:
    """Register ed-explore's contributions into the core registry surfaces.

    Phase-1 skeleton: filled in Step 5 when step_body_tour relocates here and
    registers itself (surface #3) into the merged core step table.
    """
    return None
