"""Standalone verification for the C-series boot determination layer (NO pytest).

Covers the primitives (PINs 1-7) + the 11 scene templates, with special
attention to the two Stage-1 fixes applied to gen-opus-2 (council wf_10093608-303):
  FIX 1 — STARSMACK no longer leaks a smacked+cooldown-cleared ship to NO_ROUTE.
  FIX 2 — _event_name no longer trusts a .get() capability (SEC-4 spoof closed).
Exit 0 iff every check passes. Run with the workspace venv python.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

from ed_core.boot.primitives import (
    ArrivalLatch,
    bounded_poll,
    fsd_cooldown_blocked,
    reconstruct_arrival_from_journal,
)
from ed_core.boot.scenes import (
    CSeriesState,
    DetermineContext,
    C_SERIES_SCENES,
    scene_for,
)

_fails: list[str] = []


def check(name: str, cond: bool) -> None:
    print(("  ok  " if cond else " FAIL ") + name)
    if not cond:
        _fails.append(name)


FROZEN = lambda: 0.0  # never-advancing clock


# ---- ArrivalLatch (PIN 6) ----
lat = ArrivalLatch()
check("latch: unarmed consume == False", lat.consume() is False)
lat.arm()
lat.arm()  # idempotent
check("latch: consume True exactly once", lat.consume() is True)
check("latch: second consume False", lat.consume() is False)

# ---- reconstruct (PIN 2/3) ----
check("recon: empty -> False", reconstruct_arrival_from_journal([]) is False)
check("recon: dict FSDJump -> True",
      reconstruct_arrival_from_journal([{"event": "FSDJump"}]) is True)
check("recon: dict SupercruiseExit -> False",
      reconstruct_arrival_from_journal([{"event": "SupercruiseExit"}]) is False)
check("recon: most-recent wins (FSDJump,SCExit -> False)",
      reconstruct_arrival_from_journal(
          [{"event": "FSDJump"}, {"event": "SupercruiseExit"}]) is False)
check("recon: most-recent wins (SCExit,FSDJump -> True)",
      reconstruct_arrival_from_journal(
          [{"event": "SupercruiseExit"}, {"event": "FSDJump"}]) is True)
check("recon: typed model FSDJump -> True",
      reconstruct_arrival_from_journal([SimpleNamespace(event="FSDJump")]) is True)


class _GhostFSDJump:  # class literally named like the event; .event is None
    event = None


check("recon PIN3: ghost class FSDJump(event=None) -> False (no class-name fallback)",
      reconstruct_arrival_from_journal([_GhostFSDJump()]) is False)


class _SpoofGet:  # exposes .get('event')='FSDJump' but no real .event data
    event = None

    def get(self, key, default=None):
        return "FSDJump" if key == "event" else default


check("recon FIX2/SEC-4: .get() capability spoof -> False",
      reconstruct_arrival_from_journal([_SpoofGet()]) is False)

# ---- fsd_cooldown_blocked (PIN 7) ----
check("cooldown: None -> False", fsd_cooldown_blocked(None) is False)
check("cooldown: bit18 set -> True",
      fsd_cooldown_blocked(SimpleNamespace(flags=1 << 18)) is True)
check("cooldown: bits 16/17/30 w/o 18 -> False",
      fsd_cooldown_blocked(
          SimpleNamespace(flags=(1 << 16) | (1 << 17) | (1 << 30))) is False)
check("cooldown: non-int flags -> False",
      fsd_cooldown_blocked(SimpleNamespace(flags="x")) is False)

# ---- bounded_poll (PIN 1/4) ----
r = bounded_poll(lambda: 0, lambda v: False, max_reads=3, clock=FROZEN, ceiling_s=5.0)
check("poll PIN1: frozen clock terminates (matched False, reads 3)",
      r.matched is False and r.reads == 3 and r.hit_ceiling is False and r.aborted is False)
_seq = iter([0, 0, 7, 9])
r = bounded_poll(lambda: next(_seq), lambda v: v == 7, max_reads=10, clock=FROZEN)
check("poll: first-match-wins (reads 3, value 7)",
      r.matched is True and r.reads == 3 and r.value == 7)
r = bounded_poll(lambda: 0, lambda v: True, max_reads=5, clock=FROZEN,
                 should_abort=lambda: True)
check("poll PIN4: abort-before-read -> reads 0, aborted True",
      r.aborted is True and r.reads == 0 and r.matched is False)
r = bounded_poll(lambda: 0, lambda v: False, max_reads=4, clock=FROZEN)
check("poll: cap exhausted -> matched False, reads 4", r.matched is False and r.reads == 4)
r = bounded_poll(lambda: 0, lambda v: True, max_reads=0, clock=FROZEN)
check("poll: max_reads<=0 -> reads 0, matched False", r.reads == 0 and r.matched is False)

# ---- scenes ----
check("scenes: exactly 11 templates", len(C_SERIES_SCENES) == 11)
check("scenes: one per state", {t.state for t in C_SERIES_SCENES} == set(CSeriesState))

_raised = False
try:
    C_SERIES_SCENES[0].act()
except NotImplementedError as exc:
    _raised = "[Phase-2 CV/action pending]" in str(exc)
check("scenes: act() raises NotImplementedError(Phase-2 marker)", _raised)


def ctx(**kw):
    return DetermineContext(**kw)


# FIX 1: smacked + cooldown cleared + empty route -> STARSMACK (NOT NO_ROUTE)
s = scene_for(ctx(status=None, route_empty=True, smacked=True, fsd_cooldown=False))
check("scenes FIX1: smacked+cooldown-cleared+empty-route -> STARSMACK",
      s is not None and s.state is CSeriesState.STARSMACK)
# smacked + route present -> still STARSMACK (not falling through to a held None)
s = scene_for(ctx(status=None, route_empty=False, smacked=True, fsd_cooldown=False))
check("scenes FIX1: smacked+route-present -> STARSMACK",
      s is not None and s.state is CSeriesState.STARSMACK)
# non-smacked empty-route normal-space -> NO_ROUTE (guard didn't break the normal case)
s = scene_for(ctx(status=None, route_empty=True, smacked=False, exploration_mode=False))
check("scenes: non-smacked empty-route -> NO_ROUTE",
      s is not None and s.state is CSeriesState.NO_ROUTE)
# EXPLORATION telemetry-wired (PIN 5)
_sc = SimpleNamespace(docked=False, in_supercruise=True, scooping_fuel=False, low_fuel=False)
s = scene_for(ctx(status=_sc, route_empty=True, exploration_mode=True))
check("scenes PIN5: SC+empty-route+exploration_mode -> EXPLORATION",
      s is not None and s.state is CSeriesState.EXPLORATION)

print()
if _fails:
    print(f"RESULT: FAIL ({len(_fails)} of many checks failed)")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("RESULT: PASS")
sys.exit(0)
