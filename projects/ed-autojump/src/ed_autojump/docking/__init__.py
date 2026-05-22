"""v2 docking — pre-flight checks + permission flow framework (stub)."""

from .docking import (
    DOCKING_REASONS,
    DockingDeniedReason,
    DockingOutcome,
    DockingResult,
    PreFlightFailure,
    preflight_check,
    handle_docking_grant,
)

__all__ = [
    "DOCKING_REASONS",
    "DockingDeniedReason",
    "DockingOutcome",
    "DockingResult",
    "PreFlightFailure",
    "preflight_check",
    "handle_docking_grant",
]
