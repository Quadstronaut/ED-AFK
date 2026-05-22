"""State-driven key macros: honk, escape, scoop, jump."""

from .honk import HonkOutcome, perform_honk
from .runner import EventDriver, Outcome

__all__ = ["HonkOutcome", "perform_honk", "EventDriver", "Outcome"]
