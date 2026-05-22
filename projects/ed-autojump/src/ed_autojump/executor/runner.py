"""
Test-friendly executor scaffolding.

The real bot polls `JournalTail.step()` and `StatusReader.poll()` in a
loop. For phase-by-phase verification we need a deterministic alternative:
the `EventDriver` accepts a synchronous iterator of events (from a fixture
journal) and runs the bot's response logic through them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Iterable, Optional

from ..journal.events import Event


class Outcome(Enum):
    OK = auto()
    TIMEOUT = auto()
    ABORTED = auto()
    DANGER = auto()


@dataclass
class EventDriver:
    """
    Feed events through a callback. Used both by tests (synchronous list)
    and by the live tail loop (yielded events).
    """

    handler: callable
    events_seen: int = 0

    def feed(self, events: Iterable[Event]) -> None:
        for ev in events:
            self.handler(ev)
            self.events_seen += 1
