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
    # NB: steps use this with SHORT windows as a poll cadence — never as a
    # success/failure gate (no-arbitrary-timed-waits rule).
    event_waiter: Optional[Callable[[str, float], bool]] = None

    # operator abort (panic switch / stop request). Event-driven loops MUST
    # consult this every iteration — it is the only exit that isn't a game
    # signal, and the operator's backstop now that wall-clock gates are banned.
    should_abort: Callable[[], bool] = lambda: False

    # factory returning a context manager that marks "a UI macro owns input"
    # for its duration (heat watchdog pauses). None when no watchdog is wired.
    exclusive_guard: Optional[Callable[[], Any]] = None

    # (seq, latest FSDTarget event) — seq advances once per FSDTarget the
    # tail has seen, so target_next_route can tell a NEW target from a stale
    # one. Wired by FlowRunner; the default means "no journal wiring".
    fsd_target_supplier: Callable[[], tuple] = lambda: (0, None)

    # outcome logging (recorder.record_outcome), optional
    record: Optional[Callable[[str, Any], None]] = None

    # widget-ring fine-align vision. widget_frame_grabber is the CENTRE-CROP
    # .grab — distinct from frame_grabber, which is the compass-region crop.
    # widget_ring_on_miss: "degrade" (default — a miss skips the fine pass,
    # compass-only jump proceeds; operator decision 2026-06-06, issue #1) or
    # "fail_closed" (a miss fails the required step, gating the jump).
    widget_ring_enabled: bool = False
    widget_ring_reader: Optional[Any] = None
    widget_frame_grabber: Optional[Callable[[], Any]] = None
    widget_ring_on_miss: str = "degrade"

    # cosmetic EDMCOverlay status writer (None -> no overlay). Duck-typed:
    # .step(proc, action, idx, total). Fail-soft; never blocks a step.
    overlay: Optional[Any] = None

    def log(self, outcome_type: str, payload: Any) -> None:
        if self.record is not None:
            self.record(outcome_type, payload)
