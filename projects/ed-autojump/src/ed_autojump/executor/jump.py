"""
Req 1 + 7 — jump danger filter.

Critical correctness rules (operator ground truth 2026-06-06):

- THROTTLE IS NEVER TOUCHED AFTER ENGAGING THE JUMP. The jump needs full
  throttle to fire; zeroing it mid-charge/countdown stalls the jump
  ("more speed required"). On arrival ED's own safety auto-dethrottles —
  there is nothing for us to do.
- Filter destination StarClass against the danger list on the prior
  `FSDTarget` event. The in-game plotter routes through D*/N/H/W* without
  warning (user's own journal: V886 Centauri:DA).

Deleted legacy (2026-06-06 purge — unwired code encoding untrue or
operator-rule-violating behavior gets removed, not fenced; this session
proved unwired-but-present code eventually gets wired):
- handle_start_jump: pressed SetSpeedZero on StartJump — would stall jumps.
- perform_star_escape + class pitch/throttle tables: fixed-time pitch then
  throttle with NO confirmation the star is off-screen — violates
  pitch-star-first. The LIVE arrival behavior is procedures/arrival.toml
  (throttle 0 → SC-assist orbit → orient → jump).
"""

from __future__ import annotations

from typing import Iterable, Optional

from ..fsd.danger import is_dangerous
from ..journal.events import FSDTarget


def should_refuse_target(
    target: FSDTarget,
    *,
    danger_classes: Optional[Iterable[str]] = None,
) -> bool:
    """Return True if we must refuse to engage the FSD on this target."""
    danger = frozenset(danger_classes) if danger_classes is not None else None
    return is_dangerous(target.star_class, danger)
