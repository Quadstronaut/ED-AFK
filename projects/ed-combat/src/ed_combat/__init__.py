"""ed-combat — combat domain for the ED-AFK bot.

Phase 1: an empty scaffold that reserves the slot and proves the plug-in
pattern. Its activate() registers NOTHING, and it runs SOLO in the active set
(combat cannot co-activate with another app). Real build is Phase 2 (heavy
operator edits). Depends on ed-core + ed-vision; never imports a sibling domain.
"""

from __future__ import annotations

__version__ = "0.2.0"


def activate() -> None:
    """Register ed-combat's contributions — Phase 1: nothing.

    Reserves the slot and proves the four-surface plug-in contract works with a
    no-op domain. solo=True is asserted in the cli host's active set.
    """
    return None
