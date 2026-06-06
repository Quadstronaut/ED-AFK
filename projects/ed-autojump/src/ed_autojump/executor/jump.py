"""
Req 1 + 7 — jump danger filter + star-exit escape.

Critical correctness landmines (SPEC §9.2 + operator ground truth 2026-06-06):

- THROTTLE IS NEVER TOUCHED AFTER ENGAGING THE JUMP. The jump needs full
  throttle to fire; zeroing it mid-charge/countdown stalls the jump
  ("more speed required"). On arrival ED's own safety auto-dethrottles —
  there is nothing for us to do. (The old SPEC §9.1 "zero throttle on
  StartJump" rule was WRONG and its handle_start_jump macro is deleted.)
- Filter destination StarClass against the danger list on the prior
  `FSDTarget` event. The in-game plotter routes through D*/N/H/W* without
  warning (user's own journal: V886 Centauri:DA).
- Pitch-and-throttle escape macro is class-conditional, with a safe
  fallback even if the route filter is bypassed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from ..fsd.danger import is_dangerous
from ..journal.events import Event, FSDJump, FSDTarget, StartJump
from ..keys.sender import Sender


# Per SPEC §9.2.3.
DEFAULT_CLASS_PITCH_S: dict[str, float] = {
    "K": 2.0, "G": 2.0, "F": 2.0,
    "B": 3.0, "A": 3.0, "O": 4.0, "M": 1.5,
    "L": 1.5, "T": 1.5, "Y": 1.5,
    # Danger classes — pitch values matter only if the filter is bypassed.
    "D": 4.5, "DA": 4.5, "DAB": 4.5, "DAO": 4.5, "DAZ": 4.5, "DAV": 4.5,
    "DB": 4.5, "DBZ": 4.5, "DBV": 4.5,
    "DO": 4.5, "DOV": 4.5, "DQ": 4.5,
    "DC": 4.5, "DCV": 4.5, "DX": 4.5,
    "N": 4.5, "H": 4.0,
    "W": 4.0, "WC": 4.0, "WN": 4.0, "WNC": 4.0, "WO": 4.0,
}

DEFAULT_CLASS_POST_PITCH_THROTTLE: dict[str, str] = {
    # Numpad-N key labels in our binds. SetSpeed75 = Key_Numpad_7,
    # SetSpeed50 = Key_Numpad_5, SetSpeed100 = Key_Numpad_9.
    "K": "SetSpeed75", "G": "SetSpeed75", "F": "SetSpeed75",
    "B": "SetSpeed50", "A": "SetSpeed50", "O": "SetSpeed50", "M": "SetSpeed75",
    "L": "SetSpeed100", "T": "SetSpeed100", "Y": "SetSpeed100",
    "D": "SetSpeed50", "DA": "SetSpeed50", "DB": "SetSpeed50",
    "N": "SetSpeed50", "H": "SetSpeed50",
    "W": "SetSpeed50", "WC": "SetSpeed50",
}


# handle_start_jump (SetSpeedZero on StartJump) is DELETED, not deprecated.
# It encoded a jump-killing behavior — the FSD needs full throttle through
# the whole sequence, and ED auto-dethrottles on arrival. It was unwired but
# exported; this session proved unwired-but-present code eventually gets
# wired (hold_alignment), so the landmine goes.


def should_refuse_target(
    target: FSDTarget,
    *,
    danger_classes: Optional[Iterable[str]] = None,
) -> bool:
    """Return True if we must refuse to engage the FSD on this target."""
    danger = frozenset(danger_classes) if danger_classes is not None else None
    return is_dangerous(target.star_class, danger)


@dataclass
class EscapeOutcome:
    star_class: str
    pitch_held_s: float
    throttle_action: str


def perform_star_escape(
    fsd_jump: FSDJump,
    sender: Sender,
    *,
    cached_star_class: Optional[str] = None,
    class_pitch_s: Optional[dict[str, float]] = None,
    class_throttle: Optional[dict[str, str]] = None,
    fallback_pitch_s: float = 3.0,
    sleeper: Callable[[float], None] = lambda s: None,
) -> EscapeOutcome:
    """
    Execute the post-FSDJump escape macro. SPEC §9.2.2:

    1. PitchUpButton hold for class-conditional t_pitch
    2. SetSpeedX during the pitch, where X depends on class
    3. (Heat check is left to the safety guard, not this macro)

    `cached_star_class` comes from the preceding `StartJump.StarClass`
    or `FSDTarget.StarClass` (FSDJump itself doesn't carry it for sub.
    `_overcharge_` SCO drives, per SPEC §4.2.3).
    """
    pitch_map = class_pitch_s or DEFAULT_CLASS_PITCH_S
    throttle_map = class_throttle or DEFAULT_CLASS_POST_PITCH_THROTTLE
    sc = cached_star_class or "K"
    t_pitch = pitch_map.get(sc, fallback_pitch_s)
    throttle_action = throttle_map.get(sc, "SetSpeed75")

    sender.press("PitchUpButton", hold=t_pitch)
    sender.press(throttle_action, hold=0.05)
    return EscapeOutcome(
        star_class=sc,
        pitch_held_s=t_pitch,
        throttle_action=throttle_action,
    )
