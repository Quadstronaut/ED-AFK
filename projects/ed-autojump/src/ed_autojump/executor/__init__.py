"""State-driven key macros: honk, escape, scoop, jump."""

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
from .scoop import (
    SCOOP_COMPLETE_RATIO,
    ScoopOutcome,
    ScoopResult,
    perform_scoop,
    should_scoop,
)

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
    "ScoopOutcome",
    "ScoopResult",
    "perform_scoop",
    "should_scoop",
    "SCOOP_COMPLETE_RATIO",
]
