"""
Panic-switch infrastructure for unattended overnight runs.

A `PanicSwitch` is a thread-safe boolean flag the orchestrator polls each
event boundary. External listeners (e.g. a global hotkey thread) trip the
switch; the orchestrator notices on its next tick and shuts down cleanly:
release all held keys, close the recorder, optionally restore binds.

We deliberately do NOT ship a real OS-level hotkey listener here. The
listener is platform-conditional (`keyboard`, `pynput`, or a Windows-only
RegisterHotKey thread) and varies per ED install. The switch abstraction
is the stable surface; wire whichever listener you trust to call
`switch.trip()`.

Test policy: every behaviour of PanicSwitch is exercised without a real
keypress — we trip the switch programmatically.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional


class PanicSwitch:
    """Thread-safe trip flag with an optional one-shot callback.

    Once tripped, subsequent `trip()` calls are no-ops — the callback
    fires at most once per trip-cycle. Call `reset()` if you need to
    re-arm (rarely needed in practice; panic = end of session).
    """

    def __init__(self, *, on_trip: Optional[Callable[[], None]] = None):
        self._lock = threading.Lock()
        self._tripped = False
        self._on_trip = on_trip
        self._callback_fired = False

    @property
    def tripped(self) -> bool:
        with self._lock:
            return self._tripped

    def trip(self) -> None:
        fire = False
        with self._lock:
            if not self._tripped:
                self._tripped = True
                if self._on_trip is not None and not self._callback_fired:
                    self._callback_fired = True
                    fire = True
        if fire:
            # Run the callback outside the lock so a callback that touches
            # the switch can't deadlock.
            assert self._on_trip is not None
            self._on_trip()

    def reset(self) -> None:
        """Re-arm. Useful in tests; rarely in production."""
        with self._lock:
            self._tripped = False
            self._callback_fired = False
