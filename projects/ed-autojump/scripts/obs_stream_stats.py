#!/usr/bin/env python3
"""OBS stream-stat writer for ED-AFK.

Polls the LIVE run (read-only -- never touches the bot) and writes plain-text
stat files into the CURRENT WORKING DIRECTORY, overwriting each tick, so an OBS
"Text (GDI+)" source pointed at a file updates on stream in real time.

Sources (auto-detected, override with flags):
  * the visited-systems log  ~/Documents/ed-afk-systems-visited.log
      one line per FSDJump arrival, append-only, never truncated -> TOTAL JUMPS
      (line count) and the CURRENT SYSTEM (last line).
  * the newest session JSONL ~/ed-afk-sessions/session_*.jsonl
      -> session jumps, FSD malfunctions survived, star-smacks recovered, and
      the current activity (the running procedure).

Files written to CWD (each holds ONLY its value, ready to drop straight into OBS):
  obs_jumps_total.txt     -> total jumps ever (the operator's primary counter)
  obs_jumps_session.txt   -> jumps in the current session
  obs_current_system.txt  -> the system the ship is in / just reached
  obs_activity.txt        -> a stream-friendly label of what the bot is doing
  obs_resilience.txt      -> "N FSD malfunctions survived - M star-smacks recovered"
  obs_uptime.txt          -> current session uptime, e.g. "3h 24m"
  obs_ticker.txt          -> a one-line combined ticker for a single OBS source

Usage (run it from wherever you want the .txt files to land):
    python obs_stream_stats.py            # poll forever, ~2s
    python obs_stream_stats.py --once     # write once and exit
    python obs_stream_stats.py --interval 1.0
    python obs_stream_stats.py --visited-log D:\\path\\visited.log --sessions-dir D:\\sess

Stdlib only; Ctrl+C to stop.
"""
from __future__ import annotations

import argparse
import glob
import json
import time
from datetime import datetime, timezone
from pathlib import Path

# A running procedure -> a stream-friendly one-liner. Anything unmapped falls
# back to the raw procedure name so a NEW scene still shows something sensible.
_ACTIVITY = {
    "traversal": "Jumping to the next system",
    "arrival": "Arriving - scooping fuel + honking",
    "smack_recovery": "Recovering from a star smack!",
    "connection_recovery": "Reconnecting to the servers",
    "dock": "Docking at the station",
    "dock_resume": "Launching + jumping out",
    "route_complete_park": "Route complete - parked",
    "exploration": "Exploring the system",
    "startup": "Spinning up",
    "sc_resume": "Resuming supercruise",
    "honk": "Honking the discovery scanner",
}


def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _newest_session(sessions_dir: Path) -> Path | None:
    files = sorted(glob.glob(str(sessions_dir / "session_*.jsonl")))
    return Path(files[-1]) if files else None


def _session_start(path: Path) -> datetime | None:
    # session_2026-07-12T203515.jsonl -> 2026-07-12 20:35:15 UTC
    try:
        stem = path.name[len("session_"):]
        return datetime.strptime(stem[:15], "%Y-%m-%dT%H%M%S").replace(
            tzinfo=timezone.utc)
    except (ValueError, IndexError):
        return None


def _scan_session(path: Path) -> tuple[int, int, str]:
    """One pass over the session log: (malfunctions, smacks, current activity)."""
    malf = smack = 0
    last_proc = ""
    for line in _read_text(path).splitlines():
        # cheap substring pre-filter before any json parse
        if "EngageJumpClearanceScoMalfunction" in line:
            malf += 1
        elif "StarSmackConfirmed" in line:
            smack += 1
        if '"procedure":' in line:
            try:
                payload = json.loads(line).get("payload", {})
            except (ValueError, TypeError):
                continue
            if isinstance(payload, dict) and payload.get("procedure"):
                last_proc = payload["procedure"]
    return malf, smack, last_proc


def _visited_stats(visited_log: Path, since: datetime | None) -> tuple[int, str, int]:
    """(total lines, current system, lines since `since`)."""
    lines = [ln for ln in _read_text(visited_log).splitlines() if ln.strip()]
    total = len(lines)
    current = ""
    if lines:
        # "<ts>  <system>" (two-space sep) or a bare "<system>"
        current = lines[-1].split("  ", 1)[-1].strip()
    session_jumps = 0
    if since is not None:
        for ln in lines:
            if "  " in ln:
                ts_raw = ln.split("  ", 1)[0].strip()
                try:
                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if ts >= since:
                    session_jumps += 1
    return total, current, session_jumps


def _fmt_uptime(start: datetime | None) -> str:
    if start is None:
        return "-"
    secs = max(0, int((datetime.now(timezone.utc) - start).total_seconds()))
    h, rem = divmod(secs, 3600)
    m = rem // 60
    return f"{h}h {m:02d}m" if h else f"{m}m"


def _write(name: str, value: str) -> None:
    """Overwrite one OBS stat file in CWD. Fail-soft -- never crash the loop."""
    try:
        Path(name).write_text(str(value), encoding="utf-8")
    except OSError:
        pass


def tick(visited_log: Path, sessions_dir: Path) -> str:
    session = _newest_session(sessions_dir)
    start = _session_start(session) if session else None
    total, current, session_jumps = _visited_stats(visited_log, start)
    malf, smack, proc = (0, 0, "")
    if session is not None:
        malf, smack, proc = _scan_session(session)
    activity = _ACTIVITY.get(proc, proc or "Idle") if proc else "Idle"
    uptime = _fmt_uptime(start)
    resilience = f"{malf} FSD malfunctions survived - {smack} star-smacks recovered"
    ticker = (f"JUMP {total}  |  {current or '...'}  |  {activity}  "
              f"|  {uptime} uptime  |  {resilience}")

    _write("obs_jumps_total.txt", total)
    _write("obs_jumps_session.txt", session_jumps)
    _write("obs_current_system.txt", current or "-")
    _write("obs_activity.txt", activity)
    _write("obs_resilience.txt", resilience)
    _write("obs_uptime.txt", uptime)
    _write("obs_ticker.txt", ticker)
    return ticker


def main() -> None:
    home = Path.home()
    ap = argparse.ArgumentParser(description="OBS stream-stat writer for ED-AFK")
    ap.add_argument("--visited-log", type=Path,
                    default=home / "Documents" / "ed-afk-systems-visited.log")
    ap.add_argument("--sessions-dir", type=Path,
                    default=home / "ed-afk-sessions")
    ap.add_argument("--interval", type=float, default=2.0,
                    help="poll seconds (default 2.0)")
    ap.add_argument("--once", action="store_true",
                    help="write once and exit (no loop)")
    args = ap.parse_args()

    print(f"OBS stats -> {Path.cwd()}   (visited-log: {args.visited_log})")
    if args.once:
        print(tick(args.visited_log, args.sessions_dir))
        return
    print("polling; Ctrl+C to stop")
    try:
        while True:
            print(tick(args.visited_log, args.sessions_dir), flush=True)
            time.sleep(max(0.25, args.interval))
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
