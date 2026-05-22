"""
Req 4 — honk every system.

`ExplorationFSSDiscoveryScan` is a charge-and-release key. Community
timing (SPEC §5.2.2) puts the full charge at 6000 ms. After release the
game emits `FSSDiscoveryScan` within ~2 s when fully resolved.

The honk routine here is a pure function over a clock interface so we
can test it without sleeping. Real callers pass a real-clock driver and
a real-tail event iterator.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable, Iterable, Iterator, Optional

from ..journal.events import Event, FSSDiscoveryScan
from ..keys.sender import Sender


HONK_HOLD_S = 6.0
RESOLVE_TIMEOUT_S = 8.0


class HonkResult(Enum):
    OK = auto()
    TIMEOUT = auto()
    NOT_BOUND = auto()


@dataclass
class HonkOutcome:
    result: HonkResult
    fss_event: Optional[FSSDiscoveryScan] = None
    held_for_s: float = 0.0
    waited_for_s: float = 0.0


def perform_honk(
    sender: Sender,
    events: Iterable[Event],
    *,
    hold_s: float = HONK_HOLD_S,
    timeout_s: float = RESOLVE_TIMEOUT_S,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> HonkOutcome:
    """
    Press the honk key (action ExplorationFSSDiscoveryScan) for `hold_s`
    seconds, then watch `events` for the matching FSSDiscoveryScan with
    Progress >= 1.0. Returns OK + the event, TIMEOUT, or NOT_BOUND.

    `sender`, `clock`, and `sleeper` are injected so tests don't sleep.
    `events` is an iterator that may be a list, a generator, or a live
    journal-tail iterator.
    """
    start = clock()
    try:
        sender.press("ExplorationFSSDiscoveryScan", hold=hold_s)
    except KeyError:
        return HonkOutcome(result=HonkResult.NOT_BOUND)
    held = clock() - start

    deadline = clock() + timeout_s
    for ev in events:
        if isinstance(ev, FSSDiscoveryScan) and ev.progress >= 1.0:
            waited = clock() - start - held
            return HonkOutcome(
                result=HonkResult.OK,
                fss_event=ev,
                held_for_s=held,
                waited_for_s=waited,
            )
        if clock() >= deadline:
            break
    return HonkOutcome(result=HonkResult.TIMEOUT, held_for_s=held)
