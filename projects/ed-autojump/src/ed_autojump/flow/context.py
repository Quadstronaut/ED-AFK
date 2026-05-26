"""Everything a step function may need, injected (so steps are unit-testable
with fakes and no real game / no real sleeps)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class StepContext:
    sender: Any
    clock: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep

    # vision (None when vision is off -> compass steps fail closed)
    compass_reader: Optional[Any] = None
    frame_grabber: Optional[Callable[[], Any]] = None
    align_kwargs: dict = field(default_factory=dict)
    compass_samples: int = 7

    # live state suppliers, wired by the FlowRunner
    status_supplier: Callable[[], Optional[Any]] = lambda: None
    event_time: Callable[[str], Optional[float]] = lambda name: None
    # block until `event` is logged or `timeout_s` elapses; True if seen.
    event_waiter: Optional[Callable[[str, float], bool]] = None

    # outcome logging (recorder.record_outcome), optional
    record: Optional[Callable[[str, Any], None]] = None

    def log(self, outcome_type: str, payload: Any) -> None:
        if self.record is not None:
            self.record(outcome_type, payload)
