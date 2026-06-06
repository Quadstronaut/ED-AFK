"""State-driven key macros: honk, jump danger filter, star escape."""

from .honk import HonkOutcome, perform_honk
from .jump import (
    EscapeOutcome,
    perform_star_escape,
    should_refuse_target,
)
from .runner import EventDriver, Outcome

__all__ = [
    "HonkOutcome",
    "perform_honk",
    "EventDriver",
    "Outcome",
    "EscapeOutcome",
    "perform_star_escape",
    "should_refuse_target",
]
