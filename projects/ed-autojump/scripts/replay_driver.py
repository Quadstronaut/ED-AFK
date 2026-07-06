r"""Offline journal -> REAL dispatch driver.

Feeds a captured `Journal.*.log` through the ACTUAL `FlowRunner.run_live` /
`dispatch` loop — no game running, no keys sent — to answer, deterministically
and reproducibly, ONE question: for each live event in that journal, which
procedure does the real dispatcher fire? In particular: does a logged FSDJump
arrival dispatch `arrival`?

WHY THIS EXISTS (2026-06-09): a fresh launch ran `startup`, jumped cleanly to
"Dryio Eaec NE-Y b34-0" (FSDJump logged 18:05:15Z), then NO procedure ran for
the arrival and the overlay froze on "STARTUP > HOLD_ALIGNMENT". The unanimous
council read: the dispatch TRIGGER is correct (`dispatch()` FSDJump -> arrival,
dispatcher.py:541) and the real failure was a PROCESS CRASH (unhandled
pydirectinput.FailSafeException) that left the overlay frozen. This driver
PROVES the first half: replay the captured journal and show that the real
`dispatch(FSDJump)` DOES fire `arrival` — i.e. the trigger logic is sound, so a
live miss must have been the process dying, not a dispatch bug.

EXACT CODE EXTRACTION: the only stand-ins are (a) a ReplayTail that delivers the
captured events the way a live tail would — a backlog batch, an empty poll (so
run_live arms itself: `_caught_up=True`), then the rest ONE AT A TIME as "live" —
and (b) null Status/NavRoute readers (the FSDJump->arrival path reads neither).
`_run` is replaced by a tracer so we observe the dispatch decision without
executing motor steps (no keys, no vision). Every real branch in run_live /
dispatch / _is_route_complete / the witchspace latch runs unchanged.

Usage (PowerShell, from projects/ed-autojump):
    .venv\Scripts\python scripts\replay_driver.py `
        "C:\Users\<user>\Saved Games\Frontier Developments\Elite Dangerous\Journal.2026-06-09T105946.01.log"
    # optional: --split-event StartJump (default) | --split-index N
"""

from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path

from ed_autojump.flow import FlowRunner, load_procedures
from ed_core.journal.tail import JournalTail

ROOT = Path(__file__).resolve().parents[1]


class _NullReader:
    """Stands in for Status/NavRoute readers. The FSDJump->arrival path reads
    neither (dispatch checks event name + _is_route_complete, which short-circuits
    on the un-latched NavRouteClear). poll()/current -> None keeps current live
    game state from contaminating the replay. (`current` is read by
    _navroute_state/_fresh_status as the cache fallback.)"""

    current = None

    def poll(self):
        return None


class _ReplayTail:
    """Deliver captured events the way a live JournalTail would.

    step() call sequence:
      1. the BACKLOG batch (events before the split = the pre-flight state),
      2. an EMPTY list  -> run_live sets `_caught_up=True` (+ runs _maybe_startup),
      3+. the remaining events ONE AT A TIME as "live" (dispatch fires here),
      then [] forever (and trips `on_exhaust` so the driver can stop the loop).
    """

    def __init__(self, events, split, on_live, on_exhaust):
        self._events = events
        self._split = split
        self._on_live = on_live
        self._on_exhaust = on_exhaust
        self._backlog_sent = False
        self._gap_sent = False
        self._i = split
        self._exhausted = False

    def step(self):
        if not self._backlog_sent:
            self._backlog_sent = True
            return list(self._events[: self._split])
        if not self._gap_sent:
            self._gap_sent = True
            return []  # empty poll -> _caught_up=True
        if self._i < len(self._events):
            ev = self._events[self._i]
            self._i += 1
            self._on_live(ev)
            return [ev]
        if not self._exhausted:
            self._exhausted = True
            self._on_exhaust()
        return []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("journal", type=Path, help="path to the captured Journal.*.log")
    ap.add_argument("--split-event", default="StartJump",
                    help="backlog ends at the FIRST event of this name; "
                         "everything from there replays LIVE (default StartJump)")
    ap.add_argument("--split-index", type=int, default=None,
                    help="explicit backlog/live split index (overrides --split-event)")
    args = ap.parse_args(argv)

    # Parse the journal with the REAL tail parser (no game needed).
    tail = JournalTail(args.journal.parent)
    events = list(tail.replay_file(args.journal))
    if not events:
        print("no events parsed")
        return 1

    if args.split_index is not None:
        split = max(0, min(args.split_index, len(events)))
    else:
        split = next((i for i, e in enumerate(events)
                      if getattr(e, "event", None) == args.split_event), 0)

    print("=" * 80)
    print(f"  REPLAY DRIVER  -  {args.journal.name}")
    print(f"  {len(events)} events - backlog=0..{split} - live={split}..{len(events)}")
    print(f"  split at first {args.split_event!r}" if args.split_index is None
          else f"  split at index {split}")
    print("=" * 80)

    dispatched: list[tuple[str, str]] = []   # (procedure, last_live_event)
    last_live = {"name": None, "ts": None}

    def on_live(ev):
        name = getattr(ev, "event", "?")
        last_live["name"] = name
        last_live["ts"] = getattr(ev, "timestamp", None)
        extra = ""
        for k in ("star_system", "jump_type", "star_class", "body_type"):
            v = getattr(ev, k, None)
            if v:
                extra += f" {k}={v}"
        print(f"  LIVE   {name}{extra}", flush=True)

    done = threading.Event()

    def record(outcome_type, payload):
        print(f"  RECORD {outcome_type}  {payload}", flush=True)

    procedures = load_procedures(ROOT / "procedures")

    # Build the real FlowRunner. _run is stubbed to a tracer (no motor steps).
    from ed_core.keys import NullSender
    runner = FlowRunner(
        procedures=procedures,
        sender=NullSender(),
        status_reader=_NullReader(),
        navroute_reader=_NullReader(),
        overlay=None,
        record=record,
        sleeper=lambda _s: (runner.request_stop() if done.is_set() else None),
        tail=_ReplayTail(events, split, on_live, done.set),
    )

    def _trace_run(name: str) -> None:
        ev = last_live["name"]
        dispatched.append((name, ev or "<pre-live>"))
        print(f"  >>> DISPATCH -> {name.upper()}   (trigger live event: {ev})", flush=True)

    runner._run = _trace_run  # type: ignore[method-assign]

    # run_live exits when the ReplayTail is exhausted (sleeper trips stop) or the
    # duration elapses; give a generous ceiling as a backstop.
    runner.run_live(duration_s=30.0)

    # ── verdict ──
    print("=" * 80)
    fsd_arrivals = [e for e in events[split:]
                    if getattr(e, "event", None) == "FSDJump"]
    arrival_dispatches = [d for d in dispatched if d[0] == "arrival"]
    print(f"  live FSDJump arrivals replayed : {len(fsd_arrivals)}")
    for e in fsd_arrivals:
        print(f"     - {getattr(e, 'timestamp', '?')}  {getattr(e, 'star_system', '?')}")
    print(f"  procedures the REAL dispatcher fired: "
          f"{[d[0] for d in dispatched] or 'NONE'}")
    if fsd_arrivals and arrival_dispatches:
        print("  VERDICT: dispatch(FSDJump) -> arrival FIRED for the logged "
              "arrival(s). The TRIGGER logic is sound — a live miss was the "
              "process dying, not a dispatch bug.")
    elif fsd_arrivals and not arrival_dispatches:
        print("  VERDICT: a logged FSDJump did NOT dispatch arrival in the real "
              "loop — this IS a dispatch-logic bug. Investigate _is_route_complete "
              "and the _caught_up gate.")
    else:
        print("  VERDICT: no live FSDJump in the replayed window -- re-run with a "
              "--split-index before the arrival you care about.")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
