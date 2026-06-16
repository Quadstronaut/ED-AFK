"""C-series boot DETERMINATION layer (pure telemetry — no action, no CV).

INV4: this package re-exports the PRIMITIVES ONLY. It does NOT import scenes —
importing scenes here would make `ed_core.boot` eager-load the template tuple as
a side effect, and the layering rule keeps boot dead-until-imported. Import
scenes explicitly:  `from ed_core.boot.scenes import scene_for`.

NOTHING in this package is wired into live dispatch (no register_* call, no
domain import). ed_core/__init__.py stays non-eager, so importing ed_core does
NOT import this package.
"""

from __future__ import annotations

from ed_core.boot.primitives import (
    ArrivalLatch,
    PollResult,
    bounded_poll,
    fsd_cooldown_blocked,
    reconstruct_arrival_from_journal,
)

__all__ = [
    "PollResult",
    "ArrivalLatch",
    "reconstruct_arrival_from_journal",
    "fsd_cooldown_blocked",
    "bounded_poll",
]
