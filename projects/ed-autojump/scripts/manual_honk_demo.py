r"""DEMO (2026-06-06): run the REAL `honk` procedure end-to-end -- the same
TOML, interpreter, steps, and sender the bot uses -- and show the journal
logging FSSDiscoveryScan. Re-honking an already-honked system re-logs the
event, so this is repeatable at the parked ship.

Usage: .venv\Scripts\python scripts\manual_honk_demo.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ed_core.flow.context import StepContext
from ed_core.flow.interpreter import run_procedure
from ed_core.flow.loader import load_procedures
from ed_core.keys import DirectInputSender, parse_binds
from ed_core.launcher.focus import focus_ed_window
from ed_core.status.status import StatusReader

ROOT = Path(__file__).resolve().parents[1]
ED_DIR = Path.home() / "Saved Games" / "Frontier Developments" / "Elite Dangerous"

proc = load_procedures(ROOT / "procedures")["honk"]
print(f"procedure: honk = {[(s.action, s.params) for s in proc.steps]}")

sender = DirectInputSender(parse_binds(
    ROOT / "src" / "ed_autojump" / "binds" / "ED-AFK.4.2.binds"))
status = StatusReader(ED_DIR / "Status.json")

jpath = max(ED_DIR.glob("Journal.*.log"), key=lambda p: p.stat().st_mtime)
baseline = jpath.stat().st_size  # only lines written AFTER this count


def event_waiter(event: str, timeout_s: float) -> bool:
    """Production contract: block until `event` logs or timeout; True if seen."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        with jpath.open("r", encoding="utf-8") as f:
            f.seek(baseline)
            for line in f:
                try:
                    if json.loads(line).get("event") == event:
                        return True
                except json.JSONDecodeError:
                    pass
        time.sleep(0.25)
    return False


ctx = StepContext(
    sender=sender,
    status_supplier=lambda: status.poll() or status.current,
    event_waiter=event_waiter,
)

if not focus_ed_window():
    raise SystemExit("could not focus ED -- aborting")
time.sleep(0.6)

t0 = time.monotonic()
result = run_procedure(proc, ctx)
elapsed = time.monotonic() - t0

print(f"\nresult: completed={result.completed} aborted={result.aborted} "
      f"steps={[(s.action, s.ok) for s in result.steps]} in {elapsed:.1f}s")

print("\nnew journal lines since baseline:")
with jpath.open("r", encoding="utf-8") as f:
    f.seek(baseline)
    for line in f:
        ev = json.loads(line)
        if ev.get("event") in ("FSSDiscoveryScan", "Music"):
            print(f"  {line.strip()}")

raise SystemExit(0 if result.completed else 1)
