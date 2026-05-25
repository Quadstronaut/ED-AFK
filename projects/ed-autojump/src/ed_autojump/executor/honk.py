"""
Req 4 — honk every system.

`ExplorationFSSDiscoveryScan` is a charge-and-release key. Community
timing (SPEC §5.2.2) puts the full charge at 6000 ms. After release the
game emits `FSSDiscoveryScan` within ~2 s when fully resolved.

The honk routine here is a pure function over a clock interface so we
can test it without sleeping. Real callers pass a real-clock driver and
a real-tail event iterator.

COMBAT-MODE FALLBACK (the gotcha this module guards against)
------------------------------------------------------------
`ExplorationFSSDiscoveryScan` only does anything when the ship's HUD is
in ANALYSIS mode. If the HUD happens to be in COMBAT mode, pressing the
honk key is a silent no-op: no scan charges, no `FSSDiscoveryScan`
journal event ever fires. There is no event to tell us we're in the
wrong mode — the only symptom is the absence of the success event.

So we treat "held the honk for the resolve window and saw no
FSSDiscoveryScan" as the combat-mode signal: tap `PlayerHUDModeToggle`
to flip Combat -> Analysis, then retry the honk once. If the retry also
times out we give up and report TIMEOUT.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable, Iterable, Iterator, Optional

from ..journal.events import Event, FSSDiscoveryScan
from ..keys.sender import Sender


HONK_HOLD_S = 6.0

# RESOLVE_TIMEOUT_S is the user's "hold it for like 10 seconds" budget:
# the window we wait for FSSDiscoveryScan *after* releasing the key. The
# honk hold itself is ~6 s (HONK_HOLD_S) and the scan resolves within
# ~2 s of release, so 8 s of post-release slack is comfortably past the
# point where a real honk would have reported in. If it hasn't by then,
# the ship is almost certainly in combat mode (see module docstring).
RESOLVE_TIMEOUT_S = 8.0

# Quick tap to flip the HUD between Combat and Analysis. It's a momentary
# toggle, not a hold — a default-length press is plenty.
MODE_TOGGLE_HOLD_S = 0.05


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
    # Set True if we pressed PlayerHUDModeToggle after the first watch
    # timed out (i.e. we suspected combat mode and tried to flip out of
    # it). Stays True even if the toggle's bind was missing — see
    # mode_toggle_ok for whether the keypress actually landed.
    mode_toggled: bool = False
    # True once we've re-fired the honk after a toggle. Lets callers
    # distinguish a clean first-try OK from an OK that only happened
    # because we corrected the HUD mode.
    retried: bool = False
    # True only if the PlayerHUDModeToggle keypress was actually sent.
    # False means the bind was missing (KeyError) and we degraded
    # gracefully without retrying.
    mode_toggle_ok: bool = False


def _watch_for_scan(
    events: Iterator[Event],
    *,
    deadline_at: float,
    clock: Callable[[], float],
) -> Optional[FSSDiscoveryScan]:
    """Pull events off `events` until we see a completed FSSDiscoveryScan
    or the clock passes `deadline_at`. Returns the event or None.

    SINGLE-PASS CONTRACT: `events` is consumed in place. In real use it's
    a live journal-tail iterator, so each call here advances the *same*
    cursor — there is no rewind. That's exactly what the retry needs: the
    second watch must read events that arrive AFTER the toggle+re-honk,
    not re-read events the first watch already discarded. Because we hold
    one iterator and resume from it, that falls out for free; never wrap
    `events` in `iter(list(...))` or re-call `iter()` on it.
    """
    for ev in events:
        if isinstance(ev, FSSDiscoveryScan) and ev.progress >= 1.0:
            return ev
        if clock() >= deadline_at:
            break
    return None


def _fire_honk(
    sender: Sender,
    *,
    hold_s: float,
    clock: Callable[[], float],
) -> float:
    """Press the honk key for hold_s. Returns the measured hold duration.

    May raise KeyError if ExplorationFSSDiscoveryScan isn't bound — the
    caller turns that into NOT_BOUND.
    """
    start = clock()
    sender.press("ExplorationFSSDiscoveryScan", hold=hold_s)
    return clock() - start


def perform_honk(
    sender: Sender,
    events: Iterable[Event],
    *,
    hold_s: float = HONK_HOLD_S,
    timeout_s: float = RESOLVE_TIMEOUT_S,
    retry_on_timeout: bool = True,
    mode_toggle_action: str = "PlayerHUDModeToggle",
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> HonkOutcome:
    """
    Press the honk key (action ExplorationFSSDiscoveryScan) for `hold_s`
    seconds, then watch `events` for the matching FSSDiscoveryScan with
    Progress >= 1.0. Returns OK + the event, TIMEOUT, or NOT_BOUND.

    Combat-mode fallback (default on): if the first watch times out with
    no FSSDiscoveryScan — the signature of a honk fired while the HUD is
    in COMBAT mode, where the key silently does nothing — we tap
    `mode_toggle_action` (PlayerHUDModeToggle) to flip into Analysis mode
    and re-fire the honk ONCE, watching the same event stream again. If
    that retry also times out we return TIMEOUT. Set retry_on_timeout
    False to restore the old single-shot behaviour.

    `timeout_s` is the post-release resolve window (the user's "hold it
    for ~10 s and if you see nothing, switch modes"); see RESOLVE_TIMEOUT_S.

    `sender`, `clock`, and `sleeper` are injected so tests don't sleep.
    `events` is an iterator that may be a list, a generator, or a live
    journal-tail iterator. It is treated as SINGLE-PASS: the retry resumes
    reading from the same iterator rather than rewinding it (see
    `_watch_for_scan`), which is what a live tail requires.
    """
    # Normalise to one iterator we hold for the whole call. Both the first
    # watch and the retry watch pull from THIS object, so the retry sees
    # only events that arrive after it — never a replay of consumed ones.
    ev_iter = iter(events)

    start = clock()
    try:
        held = _fire_honk(sender, hold_s=hold_s, clock=clock)
    except KeyError:
        return HonkOutcome(result=HonkResult.NOT_BOUND)

    fss = _watch_for_scan(
        ev_iter, deadline_at=clock() + timeout_s, clock=clock
    )
    if fss is not None:
        return HonkOutcome(
            result=HonkResult.OK,
            fss_event=fss,
            held_for_s=held,
            waited_for_s=clock() - start - held,
        )

    # First watch timed out with nothing. If retries are off, report it.
    if not retry_on_timeout:
        return HonkOutcome(result=HonkResult.TIMEOUT, held_for_s=held)

    # Suspected combat mode: try to flip the HUD into Analysis.
    outcome = HonkOutcome(
        result=HonkResult.TIMEOUT, held_for_s=held, mode_toggled=True
    )
    try:
        sender.press(mode_toggle_action, hold=MODE_TOGGLE_HOLD_S)
        outcome.mode_toggle_ok = True
    except KeyError:
        # PlayerHUDModeToggle isn't bound — we can't correct the mode, so
        # there's no point re-firing. Degrade gracefully: TIMEOUT with
        # mode_toggled=True but mode_toggle_ok=False so the caller can see
        # the toggle was attempted-but-impossible.
        return outcome

    # Re-fire the honk and watch the same stream once more.
    outcome.retried = True
    retry_start = clock()
    try:
        retry_held = _fire_honk(sender, hold_s=hold_s, clock=clock)
    except KeyError:
        # The honk bind vanished between tries (shouldn't happen, but be
        # safe). Keep the toggle record; report NOT_BOUND.
        return HonkOutcome(
            result=HonkResult.NOT_BOUND,
            held_for_s=held,
            mode_toggled=True,
            mode_toggle_ok=True,
            retried=True,
        )
    outcome.held_for_s = retry_held

    fss = _watch_for_scan(
        ev_iter, deadline_at=clock() + timeout_s, clock=clock
    )
    if fss is not None:
        outcome.result = HonkResult.OK
        outcome.fss_event = fss
        outcome.waited_for_s = clock() - retry_start - retry_held
    return outcome
