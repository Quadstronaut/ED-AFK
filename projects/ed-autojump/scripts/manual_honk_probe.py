r"""EXPERIMENT (2026-06-06): why does the honk never fire?

All-day telemetry: ensure_analysis_mode ok=true, then hold_until_event
(ExplorationFSSDiscoveryScan / Key_Equals) held the FULL 30s safety cap on
every attempt -- zero FSSDiscoveryScan in ANY journal, ever.

Hypothesis under test: the ExplorationFSSDiscoveryScan bind only works
INSIDE FSS mode; the cockpit honk is the FIRE-GROUP trigger (discovery
scanner in a fire group, PrimaryFire held, analysis HUD).

  Test A (reproduce): hold Key_Equals 12s from the cockpit -> expect nothing.
  Test B (hypothesis): hold PrimaryFire (Key_Numpad_Subtract) up to 10s
                       in analysis mode -> expect FSSDiscoveryScan.

Both tests are journal-gated: the key releases the instant the event logs.

Usage: .venv\Scripts\python scripts\manual_honk_probe.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ed_core.keys import DirectInputSender, parse_binds
from ed_core.launcher.focus import focus_ed_window
from ed_core.status.status import StatusReader

ROOT = Path(__file__).resolve().parents[1]
ED_DIR = Path.home() / "Saved Games" / "Frontier Developments" / "Elite Dangerous"

sender = DirectInputSender(parse_binds(
    ROOT / "src" / "ed_autojump" / "binds" / "ED-AFK.4.2.binds"))
status = StatusReader(ED_DIR / "Status.json")


def newest_journal() -> Path:
    return max(ED_DIR.glob("Journal.*.log"), key=lambda p: p.stat().st_mtime)


def hold_until_journal(action: str, event: str, max_hold_s: float) -> tuple[bool, float]:
    """Key DOWN -> poll the journal tail for `event` -> key UP. Returns
    (fired, seconds_held). Journal-gated, max_hold_s is the safety cap."""
    jpath = newest_journal()
    offset = jpath.stat().st_size          # only NEW lines count
    t0 = time.monotonic()
    sender.key_down(action)
    try:
        while time.monotonic() - t0 < max_hold_s:
            with jpath.open("r", encoding="utf-8") as f:
                f.seek(offset)
                for line in f:
                    try:
                        if json.loads(line).get("event") == event:
                            return True, time.monotonic() - t0
                    except json.JSONDecodeError:
                        pass
            time.sleep(0.25)
        return False, time.monotonic() - t0
    finally:
        sender.key_up(action)


def flags() -> str:
    st = status.poll() or status.current
    if st is None:
        return "no status"
    return (f"analysis={getattr(st, 'analysis_mode', '?')} "
            f"sc={getattr(st, 'in_supercruise', '?')} "
            f"gui={getattr(st, 'gui_focus', '?')}")


if not focus_ed_window():
    raise SystemExit("could not focus ED -- aborting")
time.sleep(0.6)
print(f"state: {flags()}")

print("\nTest A: ExplorationFSSDiscoveryScan (Key_Equals), 12s cap...")
fired_a, held_a = hold_until_journal(
    "ExplorationFSSDiscoveryScan", "FSSDiscoveryScan", 12.0)
print(f"  A: fired={fired_a} after {held_a:.1f}s")

if fired_a:
    print("\nVERDICT: the FSS bind DOES honk from the cockpit -- the bug is elsewhere.")
    raise SystemExit(0)

time.sleep(2.0)  # let the failed hold settle before the next input

print("\nTest B: PrimaryFire (Key_Numpad_Subtract), 10s cap...")
fired_b, held_b = hold_until_journal("PrimaryFire", "FSSDiscoveryScan", 10.0)
print(f"  B: fired={fired_b} after {held_b:.1f}s")

if fired_b:
    print("\nVERDICT: cockpit honk = FIRE-GROUP trigger. Fix honk.toml to hold PrimaryFire.")
    raise SystemExit(0)
print("\nVERDICT: neither bind honked -- check the discovery scanner's fire-group assignment in-game.")
raise SystemExit(1)
