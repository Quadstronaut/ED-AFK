"""Standalone verification for the C-series boot determination layer (NO pytest).

Covers the primitives (PINs 1-7) + the 11 scene templates, with special
attention to the two Stage-1 fixes applied to gen-opus-2 (council wf_10093608-303):
  FIX 1 — STARSMACK no longer leaks a smacked+cooldown-cleared ship to NO_ROUTE.
  FIX 2 — _event_name no longer trusts a .get() capability (SEC-4 spoof closed).
  proc-field check — every template carries proc: str|None mirroring _STATE_TO_PROC;
    no `act` attribute survives; the three run-state proc strings are exact.
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
    scene_by_state,
)
from ed_autojump.flow.boot_routes import _STATE_TO_PROC

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

# proc-field check: every template carries proc: str|None; no `act` attribute remains
check("scenes: every template has proc attr (str|None)",
      all(hasattr(t, "proc") and (t.proc is None or isinstance(t.proc, str))
          for t in C_SERIES_SCENES))
check("scenes: no template has act attr (fork-logic trap gone)",
      all(not hasattr(t, "act") for t in C_SERIES_SCENES))
check("scenes: module has no _act_pending",
      not hasattr(__import__("ed_core.boot.scenes", fromlist=["_act_pending"]), "_act_pending"))
check("scenes: module has no _PHASE2",
      not hasattr(__import__("ed_core.boot.scenes", fromlist=["_PHASE2"]), "_PHASE2"))
# proc mirrors the live _STATE_TO_PROC (T5 — cross-module conformance, INV8)
_proc_mirror_ok = True
for _state in CSeriesState:
    _kind, _payload = _STATE_TO_PROC[_state]
    _tmpl = scene_by_state(_state)
    if _kind == "run":
        if _tmpl is None or _tmpl.proc != _payload:
            _proc_mirror_ok = False
    else:
        if _tmpl is None or _tmpl.proc is not None:
            _proc_mirror_ok = False
check("scenes: proc mirrors live _STATE_TO_PROC for all 11 states", _proc_mirror_ok)


def ctx(**kw):
    return DetermineContext(**kw)


# FIX 1 (updated for BUG B / C3 redesign): smacked alone is no longer sufficient
# to enter STARSMACK — _det_starsmack now requires CV confirmation (smack_kind).
# Without smack_kind, it abstains (None) so scene_for does NOT select STARSMACK
# (INV1/BUG B fix). With smack_kind set, STARSMACK IS selected.
s = scene_for(ctx(status=None, route_empty=True, smacked=True, fsd_cooldown=False))
check("scenes FIX1: smacked+no-cv+empty-route -> abstain (NOT STARSMACK, BUG B fix)",
      s is None or s.state is not CSeriesState.STARSMACK)
s = scene_for(ctx(status=None, route_empty=False, smacked=True, fsd_cooldown=False))
check("scenes FIX1: smacked+no-cv+route-present -> abstain (NOT STARSMACK, BUG B fix)",
      s is None or s.state is not CSeriesState.STARSMACK)
# CV-confirmed smack DOES enter STARSMACK (the positive path still works):
s = scene_for(ctx(status=None, route_empty=True, smacked=True, smack_kind="star"))
check("scenes FIX1: smacked+smack_kind=star -> STARSMACK selected",
      s is not None and s.state is CSeriesState.STARSMACK)
s = scene_for(ctx(status=None, route_empty=True, smacked=True, smack_kind="planet"))
check("scenes FIX1: smacked+smack_kind=planet -> STARSMACK selected",
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
