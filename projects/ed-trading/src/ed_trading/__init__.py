"""ed-trading — trading domain for the ED-AFK bot.

Phase 1: an empty scaffold that reserves the slot and proves the plug-in
pattern. Its activate() registers NOTHING. Unlike solo combat, ed-trading is
AUTO-DISCOVERED — it declares `trading = ed_trading:activate` in the
`ed_autojump.plugins` entry-point group (explore-style), so the CLI plug-in
loop (cli.py: `for _ep in entry_points(group="ed_autojump.plugins"): _ep.load()()`)
co-activates it additively at startup, alongside ed_autojump.activate().

Because that loop may co-run activate() with a direct call (and the loop wraps
no error handling), activate() carries the same `_activated` idempotency guard
ed_autojump/ed_explore use — combat's stub omits it because combat is never
auto-discovered. Calling activate() twice is a safe no-op. Depends on ed-core +
ed-vision; never imports a sibling domain.

PIT-STOP NOTE (OUT OF SCOPE — NOT implemented, reserves NO code): a future
mid-route docking pit-stop that SELLS high-value cargo at an intermediate
station before the final destination, then RE-BUYS to refill — an optimization
for multi-condition trade trips (sell-high-then-rebuy). Phase 1 implements none
of it; this is a note only.
"""

from __future__ import annotations

__version__ = "0.2.0"

# Idempotency guard (mirrors ed_autojump / ed_explore). The entry-point loop in
# cli.py wraps `_ep.load()()` in NO try/except, so activate() must never raise
# and must be safe to call more than once.
_activated = False


def activate() -> None:
    """Register ed-trading's contributions — Phase 1: NOTHING.

    Reserves the slot and proves the additive entry-point contract works with a
    no-op domain. Idempotent: the first call latches `_activated`; every later
    call short-circuits and returns None without touching any registry surface.
    Registers no steps, no classifier, no event-routes, no procedure dir — so it
    can never trip ed-core's fail-on-duplicate register_step guard.
    """
    global _activated
    if _activated:
        return
    _activated = True
    return None
