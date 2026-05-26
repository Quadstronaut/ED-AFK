"""State-driven key macros: honk, jump."""

from .honk import HonkOutcome, perform_honk
from .jump import (
    ChargeOutcome,
    ChargeResult,
    EscapeOutcome,
    handle_start_jump,
    perform_star_escape,
    should_refuse_target,
)
from .runner import EventDriver, Outcome

__all__ = [
    "HonkOutcome",
    "perform_honk",
    "EventDriver",
    "Outcome",
    "ChargeOutcome",
    "ChargeResult",
    "EscapeOutcome",
    "handle_start_jump",
    "perform_star_escape",
    "should_refuse_target",
]
