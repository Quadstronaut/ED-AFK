"""Typed, immutable representation of a procedure and its steps."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class Step:
    """One action in a procedure. `params` is everything from the TOML inline
    table except `action` and `required`."""
    action: str
    params: dict[str, Any] = field(default_factory=dict)
    required: bool = False


@dataclass(frozen=True)
class OnRequiredFail:
    """What to do when a `required` step fails. Default = abort immediately."""
    retry_from: Optional[str] = None   # action name to resume from
    max_retries: int = 0
    backoff_s: float = 0.0


@dataclass(frozen=True)
class Procedure:
    name: str
    steps: tuple[Step, ...]
    parallel: bool = False                 # this procedure is a background track
    stop_on_event: Optional[str] = None    # journal event that ends a parallel track
    timeout_s: float = 0.0                 # hard cap for a parallel track (0 = none)
    parallel_tracks: tuple[str, ...] = ()  # procedures to launch concurrently at start
    on_required_fail: OnRequiredFail = field(default_factory=OnRequiredFail)

    def index_of_action(self, action: str) -> Optional[int]:
        """Index of the FIRST step whose action == `action`, else None."""
        for i, s in enumerate(self.steps):
            if s.action == action:
                return i
        return None
