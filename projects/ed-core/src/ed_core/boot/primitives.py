"""C-series boot DETERMINATION primitives — pure telemetry, no action, no CV.

This module is the primitive contract for the 11-state C-series boot
determination (spec: docs/superpowers/specs/2026-06-15-cseries-boot-determination-spec.md).
It implements the 7 PINS verbatim and the 4 LOCKED PATTERNS' telemetry halves.

LAYERING: ed_core (DAG rank 1) — imports stdlib + ed_vision only. This file
imports NEITHER (stdlib only). It NEVER imports a domain package and NEVER calls
register_*; nothing here is wired into live dispatch. (INV1/INV2.)

NOTHING HERE TOUCHES THE SHIP. Determination is read-only over telemetry. The
ACTION half (orbit, align, throttle, FSS) is Phase-2 (see scenes.SceneTemplate.proc / the live _STATE_TO_PROC map).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional


# ---------------------------------------------------------------------------
# PIN 4 — PollResult with an explicit `aborted` field
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PollResult:
    """Outcome of a bounded poll (LP3).

    The terminal conditions are mutually distinguishable:
      - matched=True                          -> predicate fired (value set)
      - aborted=True, reads=0                  -> abort before the first read
      - aborted=True, reads>0                  -> abort after some reads
      - hit_ceiling=True                       -> advisory clock ceiling tripped
      - matched=False, aborted=False, no ceil  -> read-count cap exhausted

    `aborted` and `hit_ceiling` are independent flags; a frozen-clock cap
    exhaustion sets neither. Frozen (immutable) so a result can't be mutated
    after the fact by a caller.
    """

    matched: bool
    reads: int
    hit_ceiling: bool
    aborted: bool
    value: Optional[Any] = None


# ---------------------------------------------------------------------------
# PIN 6 / LP1 — ArrivalLatch (exactly-once consume, idempotent arm)
# ---------------------------------------------------------------------------

class ArrivalLatch:
    """Single-shot arrival latch for LP1 (FSDJump arrival).

    CLASS INVARIANT — SINGLE-THREADED (PIN 6): the engine dispatch loop is
    single-threaded and is the SOLE owner of a latch instance. arm()/consume()
    are therefore NOT guarded by a lock — adding one would imply a concurrency
    model the engine does not have and would be dead synchronization. If a
    future design ever shares a latch across threads, that is a contract change
    and a lock must be introduced deliberately, not retrofitted by accident.

    Semantics:
      - arm() is IDEMPOTENT: arming an already-armed latch is a no-op.
      - consume() returns True EXACTLY ONCE after an arm, then False until the
        next arm. Consuming an un-armed latch returns False.
    """

    __slots__ = ("_armed",)

    def __init__(self) -> None:
        self._armed: bool = False

    def arm(self) -> None:
        """Latch an arrival. Idempotent."""
        self._armed = True

    def consume(self) -> bool:
        """Consume the latch. True the first call after arm(), then False."""
        if self._armed:
            self._armed = False
            return True
        return False

    @property
    def armed(self) -> bool:
        """Is the latch currently armed (and not yet consumed)?"""
        return self._armed

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"ArrivalLatch(armed={self._armed})"


# ---------------------------------------------------------------------------
# PIN 3 / PIN 2 / LP1 — reconstruct arrival from journal
# ---------------------------------------------------------------------------

# PIN 2 — fixed arrival event semantics. Most-recent QUALIFYING event decides.
#   FSDJump          -> arrival          (True)
#   SupercruiseExit  -> NOT arrival      (False)  [routine normal-space drop]
#   SupercruiseEntry -> NOT arrival      (False)
# Any other event is a non-qualifier and is skipped (does not reset the verdict).
_ARRIVAL_TRUE = "FSDJump"
_ARRIVAL_FALSE = frozenset({"SupercruiseExit", "SupercruiseEntry"})


def _event_name(ev: Any) -> Optional[str]:
    """Resolve a journal event's NAME from data — dict OR typed model (PIN 3).

    Resolution order, data-only:
      1. raw dict (journal line):  ev['event']
      2. typed model:              ev.event attribute

    Exactly the spec's "typed model OR raw dict" contract — no wider capability
    trust. An arbitrary object that merely exposes a `.get('event')` method (a
    Mock, a non-dict mapping) is NOT treated as a journal line (closes the SEC-4
    spoof). And NEVER type(ev).__name__: a class literally named `FSDJump` exists
    in ed_core.journal.events; we read the .event DATA (not the type name), so a
    ghost FSDJump whose .event is None resolves to None and does NOT count. A
    real typed FSDJump carries the Literal["FSDJump"] .event and counts.

    Returns None when no event name is present in the data.
    """
    # 1. Raw dict journal line (isinstance covers dict subclasses too).
    if isinstance(ev, dict):
        name = ev.get("event")
        return name if isinstance(name, str) and name else None

    # 2. Typed model attribute — a DATA read of .event, NOT type(ev).__name__.
    name = getattr(ev, "event", None)
    return name if isinstance(name, str) and name else None


def reconstruct_arrival_from_journal(events: Iterable[Any]) -> bool:
    """Did the most-recent qualifying journal event indicate a hyperspace arrival?

    Walks `events` (newest-LAST — natural journal order) and remembers the
    verdict of the most-recent FSDJump / SupercruiseExit / SupercruiseEntry.
    Returns that verdict; False if no qualifying event is present.

    Dual input (PIN 3): each element may be a raw dict ({'event': 'FSDJump'})
    OR a typed model with a .event attribute. Name is resolved from DATA only;
    no class-name fallback, so the ghost-arrival spoof (a class named FSDJump
    with event=None) does NOT count.

    Fixed semantics (PIN 2): FSDJump->True, SupercruiseExit->False,
    SupercruiseEntry->False.
    """
    verdict = False
    for ev in events:
        name = _event_name(ev)
        if name == _ARRIVAL_TRUE:
            verdict = True
        elif name in _ARRIVAL_FALSE:
            verdict = False
        # non-qualifying events do not touch the verdict
    return verdict


# ---------------------------------------------------------------------------
# PIN 7 / LP2 — fsd_cooldown_blocked (bit 18 only)
# ---------------------------------------------------------------------------

_FSD_COOLDOWN_BIT = 1 << 18  # StatusFlags.FsdCooldown — cross-checked vs status.py


def fsd_cooldown_blocked(status: Any | None) -> bool:
    """Is the FSD on COOLDOWN per Status `Flags` bit 18 (LP2)?

    Consumer is BLOCK-DETECTION (PIN 7): "do not assert a block without
    evidence." Therefore:
      - status is None        -> False (no evidence -> not blocked)
      - bit 18 set            -> True
      - bits 16/17/30 w/o 18  -> False (mass-lock / charging / jump are NOT
                                        cooldown; only bit 18 is)

    Reads `status.flags` (the parsed Status int field). A status object missing
    a numeric `flags` is treated as no evidence -> False.
    """
    if status is None:
        return False
    flags = getattr(status, "flags", None)
    if not isinstance(flags, int):
        return False
    return bool(flags & _FSD_COOLDOWN_BIT)


# ---------------------------------------------------------------------------
# PIN 1 / PIN 4 / LP3 — bounded_poll (READ-COUNT-bounded)
# ---------------------------------------------------------------------------

def bounded_poll(
    read: Callable[[], Any],
    predicate: Callable[[Any], bool],
    *,
    max_reads: int,
    clock: Callable[[], float] = time.monotonic,
    ceiling_s: Optional[float] = None,
    sleeper: Optional[Callable[[float], None]] = None,
    poll_interval_s: float = 0.0,
    should_abort: Optional[Callable[[], bool]] = None,
) -> PollResult:
    """Poll `read()` until `predicate` matches, bounded by READ COUNT (PIN 1/LP3).

    THE BOUND IS `max_reads` — a read-count cap. This is what guarantees
    termination. The wall-clock `ceiling_s` is an ADVISORY EARLY EXIT ONLY: a
    frozen or never-advancing clock (clock=lambda: 0.0) MUST NOT cause an
    infinite loop, because the loop is bounded by the read count, not the clock.
    This is the #1 defect the prior run shipped (clock-deadline-only loop hung
    under a frozen clock); the read-count cap is the fix.

    Order of checks each iteration (abort is first-class, PIN 4):
      1. should_abort()  -> abort BEFORE reading (so abort-before-first-read
                            yields reads=0, aborted=True). Also checked each loop.
      2. read() + predicate -> first match wins (returns value).
      3. advisory ceiling -> if a real clock has passed ceiling_s, early-exit
                            with hit_ceiling=True (NOT the termination guarantee).
      4. cap -> after max_reads reads with no match, return matched=False.

    Sleeping between reads (sleeper(poll_interval_s)) is skipped after the final
    read and skipped on abort, so an aborting poll drains promptly. With a no-op
    sleeper the call is pure CPU and still bounded by max_reads.

    Returns a PollResult; see its docstring for the terminal-condition table.
    """
    # max_reads is the hard bound. Coerce defensively: a non-positive cap means
    # "no reads permitted" -> immediate matched=False, reads=0 (not an infinite
    # loop, not a single freebie read).
    try:
        cap = int(max_reads)
    except (TypeError, ValueError):
        cap = 0
    if cap < 0:
        cap = 0

    start = clock()
    reads = 0

    while reads < cap:
        # 1. Abort BEFORE the read so abort-before-first-read => reads=0.
        if should_abort is not None and should_abort():
            return PollResult(
                matched=False, reads=reads, hit_ceiling=False,
                aborted=True, value=None,
            )

        # 2. Read + test. First match wins.
        value = read()
        reads += 1
        if predicate(value):
            return PollResult(
                matched=True, reads=reads, hit_ceiling=False,
                aborted=False, value=value,
            )

        # 3. Advisory clock ceiling — EARLY EXIT ONLY, never the bound. Under a
        #    frozen clock `now - start` stays 0, so this never trips and the
        #    read-count cap below is what terminates the loop.
        if ceiling_s is not None:
            now = clock()
            if (now - start) >= ceiling_s:
                return PollResult(
                    matched=False, reads=reads, hit_ceiling=True,
                    aborted=False, value=None,
                )

        # 4. Sleep between reads (skip after the last read; skip when no-op).
        if reads < cap and sleeper is not None and poll_interval_s > 0.0:
            sleeper(poll_interval_s)

    # Read-count cap exhausted with no match. Termination is GUARANTEED here.
    return PollResult(
        matched=False, reads=reads, hit_ceiling=False,
        aborted=False, value=None,
    )


__all__ = [
    "PollResult",
    "ArrivalLatch",
    "reconstruct_arrival_from_journal",
    "fsd_cooldown_blocked",
    "bounded_poll",
]
