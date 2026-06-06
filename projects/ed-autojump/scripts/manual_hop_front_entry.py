r"""EXPERIMENT (2026-06-06 16:38): does aligning FRONT to the locked ROUTE
HOP produce SC entry where 12+ minutes of anti-star alignment didn't?

Run 12's accidental method: the locked destination's compass dot front-
centred -> entry in ~60s. v4's anti-star alignment: three 240s watchdogs.
Lock the hop, align front, time the entry.

Usage: .venv\Scripts\python scripts\manual_hop_front_entry.py
"""

from __future__ import annotations

import time
from pathlib import Path

from ed_autojump.config import load_config
from ed_autojump.executor.align import _correct, _measure, align_to_target
from ed_autojump.keys import DirectInputSender, parse_binds
from ed_autojump.launcher.focus import focus_ed_window
from ed_autojump.status.status import StatusReader
from ed_autojump.vision.capture import build_vision

ROOT = Path(__file__).resolve().parents[1]
cfg = load_config(ROOT / "config.toml")
reader, grab = build_vision(cfg)
assert reader is not None, "vision unavailable"
sender = DirectInputSender(parse_binds(
    ROOT / "src" / "ed_autojump" / "binds" / "ED-AFK.4.2.binds"))
status = StatusReader(Path.home() / "Saved Games" / "Frontier Developments"
                      / "Elite Dangerous" / "Status.json")

if not focus_ed_window():
    raise SystemExit("could not focus ED -- aborting")
time.sleep(0.6)

print("locking the route hop...")
sender.press("TargetNextRouteSystem", hold=0.05)
time.sleep(1.5)

t_start = time.monotonic()
print("aligning FRONT to the locked hop...")
out = align_to_target(reader, sender, capture=grab, samples=5, timeout_s=90)
print(f"align: {out.aligned} ({out.reason}, iters={out.iterations})")

print("holding front alignment, watching for entry (300s ceiling)...")
t0 = time.monotonic()
while time.monotonic() - t0 < 300:
    st = status.poll() or status.current
    if st is not None:
        if getattr(st, "in_supercruise", False):
            print(f"SC ENTRY after {time.monotonic() - t_start:.0f}s total")
            raise SystemExit(0)
        if getattr(st, "fsd_jump", False):
            print(f"JUMP COMMIT after {time.monotonic() - t_start:.0f}s total")
            raise SystemExit(0)
    read = _measure(reader, grab, 3)
    if read.found and read.in_front and read.magnitude > 0.07:
        _correct(sender, read, gain=0.3, min_press=0.04, max_press=0.10,
                 deadzone=0.05)
    time.sleep(0.8)
print("no entry within 300s of hop-front alignment either")
raise SystemExit(1)
