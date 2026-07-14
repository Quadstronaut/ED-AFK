#!/usr/bin/env python3
"""honk_latency_check.py — live-validation cross-check for the CLASS B honk fix.

Compares a bot session recorder JSONL (LoggingSender ``action`` rows) against
the ED journal's FSSDiscoveryScan events and asserts the traversal-honk latency
thresholds from the Stage-0 spec (§4). Pure stdlib; runs offline on captured
files, so it is safe to fold into a post-session gate without the game running.

A HONK (hold) is delimited FIRST ``<bind>:down`` .. next ``<bind>:up``. With the
keep-alive fix the recorder legitimately shows REPEATED ``<bind>:down`` rows
inside one hold (the <=1.0s re-assert cadence) — those are folded into the same
hold. Exactly ONE ``<bind>:up`` per honk remains required.

PASS (per the spec) requires, for EVERY honk:
  * hold = (up_ts - first_down_ts)                    <= HOLD_MAX_S   (8.0s)
  * (nearest journal FSSDiscoveryScan - first_down)   <= SCAN_MAX_S   (7.0s)
  * hold                                              <= HARD_MAX_S   (10.0s) [zero over]
and globally:
  * no ``release_all`` action row inside any hold window
  * an ensure_analysis_mode Step-ok precedes each honk's first ``<bind>:down``
  * no NEW ParallelTrackSkipped(already_holding) beyond --baseline-skips

Exit code 0 on PASS, 1 on FAIL (or on missing inputs).

Usage:
    python honk_latency_check.py SESSION.jsonl [--journal-dir DIR]
        [--bind PrimaryFire] [--event FSSDiscoveryScan]
        [--baseline-skips 0] [--verbose]

Defaults: newest session in ED_AFK_SESSIONS_DIR or ~/ed-afk-sessions, and the
journal dir at ~/Saved Games/Frontier Developments/Elite Dangerous.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# --- thresholds (spec §4 / AC7) ---------------------------------------------
HOLD_MAX_S = 8.0     # up - first_down
SCAN_MAX_S = 7.0     # nearest journal scan - first_down
HARD_MAX_S = 10.0    # zero honks may exceed this


# --- time parsing ------------------------------------------------------------

def _parse_ts(s: str) -> Optional[float]:
    """ISO-8601 UTC ('...Z' or offset) -> epoch seconds. Both the recorder
    (millisecond) and the ED journal (second) stamps are UTC; compare directly."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


# --- input discovery ---------------------------------------------------------

def _default_session() -> Optional[Path]:
    env = os.environ.get("ED_AFK_SESSIONS_DIR")
    base = Path(env) if env else (Path.home() / "ed-afk-sessions")
    if not base.is_dir():
        return None
    sessions = sorted(base.glob("session_*.jsonl"))
    return sessions[-1] if sessions else None


def _default_journal_dir() -> Path:
    return (Path.home() / "Saved Games" / "Frontier Developments"
            / "Elite Dangerous")


# --- parsing -----------------------------------------------------------------

def _load_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _journal_scan_times(journal_dir: Path, event: str) -> list[float]:
    """Every `event` (FSSDiscoveryScan) timestamp across all Journal*.log,
    read from the INTERNAL UTC 'timestamp' field (the filename's local stamp
    is irrelevant)."""
    times: list[float] = []
    for jf in sorted(journal_dir.glob("Journal*.log")):
        try:
            with jf.open(encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if not line or '"event"' not in line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("event") == event:
                        t = _parse_ts(ev.get("timestamp", ""))
                        if t is not None:
                            times.append(t)
        except OSError:
            continue
    return sorted(times)


class Honk:
    __slots__ = ("first_down", "up", "down_count", "index")

    def __init__(self, index: int, first_down: float):
        self.index = index
        self.first_down = first_down
        self.up: Optional[float] = None
        self.down_count = 1  # this first down


def _extract_honks(rows: list[dict], bind: str) -> list[Honk]:
    """Walk action rows in file order; fold re-assert downs into one hold."""
    down = f"{bind}:down"
    up = f"{bind}:up"
    honks: list[Honk] = []
    cur: Optional[Honk] = None
    for r in rows:
        if r.get("kind") != "action":
            continue
        act = r.get("action")
        ts = _parse_ts(r.get("ts", ""))
        if ts is None:
            continue
        if act == down:
            if cur is None:
                cur = Honk(len(honks), ts)
                honks.append(cur)
            else:
                cur.down_count += 1  # keep-alive re-assert, same hold
        elif act == up:
            if cur is not None:
                cur.up = ts
                cur = None
            # stray up with no open hold -> ignored (reported by well-formed check)
    return honks


def _release_all_times(rows: list[dict]) -> list[float]:
    out = []
    for r in rows:
        if r.get("kind") == "action" and r.get("action") == "release_all":
            t = _parse_ts(r.get("ts", ""))
            if t is not None:
                out.append(t)
    return out


def _analysis_ok_times(rows: list[dict]) -> list[float]:
    """Timestamps of successful ensure_analysis_mode step completions.

    Primary signal: the interpreter's per-step outcome
    {"kind":"outcome","outcome_type":"Step","payload":{"action":"ensure_analysis_mode","ok":true}}.
    """
    out = []
    for r in rows:
        if r.get("kind") != "outcome" or r.get("outcome_type") != "Step":
            continue
        p = r.get("payload") or {}
        if p.get("action") == "ensure_analysis_mode" and p.get("ok") is True:
            t = _parse_ts(r.get("ts", ""))
            if t is not None:
                out.append(t)
    return sorted(out)


def _skip_already_holding_count(rows: list[dict]) -> int:
    n = 0
    for r in rows:
        if r.get("kind") == "outcome" and r.get("outcome_type") == "ParallelTrackSkipped":
            p = r.get("payload") or {}
            if p.get("reason") == "already_holding":
                n += 1
    return n


def _nearest(scans: list[float], t: float) -> Optional[float]:
    if not scans:
        return None
    return min(scans, key=lambda s: abs(s - t))


# --- report ------------------------------------------------------------------

def run(session: Path, journal_dir: Path, *, bind: str, event: str,
        baseline_skips: int, verbose: bool) -> int:
    rows = _load_rows(session)
    scans = _journal_scan_times(journal_dir, event)
    honks = _extract_honks(rows, bind)
    releases = _release_all_times(rows)
    analysis_ok = _analysis_ok_times(rows)
    skip_count = _skip_already_holding_count(rows)

    print(f"session : {session}")
    print(f"journal : {journal_dir}  ({len(scans)} {event} events)")
    print(f"honks   : {len(honks)}")
    print()

    failures: list[str] = []

    if not honks:
        failures.append("no honks found (no "
                        f"{bind}:down/{bind}:up pairs in session)")

    prev_up = None
    hdr = (f"{'#':>2}  {'hold_s':>7}  {'scan-down':>9}  {'downs':>5}  "
           f"{'analysis?':>9}  {'no_rel?':>7}  verdict")
    print(hdr)
    print("-" * len(hdr))

    for h in honks:
        row_fail: list[str] = []
        if h.up is None:
            hold_s = float("nan")
            row_fail.append("no matching :up (hold never closed)")
        else:
            hold_s = h.up - h.first_down
            if hold_s > HOLD_MAX_S:
                row_fail.append(f"hold {hold_s:.2f}s > {HOLD_MAX_S}s")
            if hold_s > HARD_MAX_S:
                row_fail.append(f"hold {hold_s:.2f}s > HARD {HARD_MAX_S}s")

        nearest = _nearest(scans, h.first_down)
        if nearest is None:
            scan_delta = float("nan")
            row_fail.append("no journal scan at all")
        else:
            scan_delta = nearest - h.first_down
            if scan_delta > SCAN_MAX_S:
                row_fail.append(f"scan-down {scan_delta:.2f}s > {SCAN_MAX_S}s")

        # ensure_analysis_mode ok since the previous hold and before this down
        lo = prev_up if prev_up is not None else float("-inf")
        analysis_here = any(lo < t <= h.first_down for t in analysis_ok)
        if not analysis_here:
            row_fail.append("no ensure_analysis_mode ok before down")

        # no release_all inside this hold window
        no_release = True
        if h.up is not None:
            inside = [r for r in releases if h.first_down <= r <= h.up]
            if inside:
                no_release = False
                row_fail.append(f"release_all inside hold ({len(inside)})")

        verdict = "PASS" if not row_fail else "FAIL"
        print(f"{h.index:>2}  {hold_s:>7.2f}  {scan_delta:>9.2f}  "
              f"{h.down_count:>5}  {str(analysis_here):>9}  "
              f"{str(no_release):>7}  {verdict}")
        if row_fail:
            for f in row_fail:
                failures.append(f"honk {h.index}: {f}")
            if verbose:
                for f in row_fail:
                    print(f"      - {f}")
        prev_up = h.up if h.up is not None else prev_up

    print()
    if skip_count > baseline_skips:
        failures.append(
            f"ParallelTrackSkipped(already_holding)={skip_count} "
            f"> baseline {baseline_skips}")
    print(f"ParallelTrackSkipped(already_holding): {skip_count} "
          f"(baseline {baseline_skips})")

    print()
    if failures:
        print(f"RESULT: FAIL ({len(failures)} issue(s))")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — every honk within thresholds")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session", nargs="?", type=Path,
                    help="session JSONL (default: newest in ~/ed-afk-sessions)")
    ap.add_argument("--journal-dir", type=Path, default=_default_journal_dir(),
                    help="ED journal directory (default: ~/Saved Games/...)")
    ap.add_argument("--bind", default="PrimaryFire",
                    help="hold bind action name (default: PrimaryFire)")
    ap.add_argument("--event", default="FSSDiscoveryScan",
                    help="release-gating journal event (default: FSSDiscoveryScan)")
    ap.add_argument("--baseline-skips", type=int, default=0,
                    help="allowed ParallelTrackSkipped(already_holding) count")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    session = args.session or _default_session()
    if session is None or not session.is_file():
        print("ERROR: no session JSONL found (pass one explicitly).",
              file=sys.stderr)
        return 1
    if not args.journal_dir.is_dir():
        print(f"ERROR: journal dir not found: {args.journal_dir}",
              file=sys.stderr)
        return 1

    return run(session, args.journal_dir, bind=args.bind, event=args.event,
               baseline_skips=args.baseline_skips, verbose=args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
