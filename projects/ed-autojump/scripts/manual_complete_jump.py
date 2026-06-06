r"""Complete a LIVE blocked hyperspace charge: align with the locked
destination's compass dot and hold until the jump commits.

2026-06-06 15:24 scene: arrival's engage_jump left a blocked-but-live
hyperspace charge to the (safe, M-class) route hop; the smack procedure's
anti-star alignment fights it. One-shot: orient TO the dot, gentle holds,
exit on the fsd_jump Status bit or a 240s ceiling.

Usage: .venv\Scripts\python scripts\manual_complete_jump.py
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
binds = parse_binds(ROOT / "src" / "ed_autojump" / "binds" / "ED-AFK.4.2.binds")
sender = DirectInputSender(binds)
status = StatusReader(Path.home() / "Saved Games" / "Frontier Developments"
                      / "Elite Dangerous" / "Status.json")

if not focus_ed_window():
    raise SystemExit("could not focus ED -- aborting")
time.sleep(0.6)

print("coarse align to the locked destination...")
out = align_to_target(reader, sender, capture=grab, samples=5, timeout_s=60)
print(f"align: {out.aligned} ({out.reason}, iters={out.iterations})")

print("holding alignment until the jump commits (240s ceiling)...")
t0 = time.monotonic()
while time.monotonic() - t0 < 240:
    st = status.poll() or status.current
    if st is not None:
        if getattr(st, "fsd_jump", False):
            print("JUMP COMMITTED (fsd_jump bit)")
            raise SystemExit(0)
        if getattr(st, "in_supercruise", False):
            print("supercruise entry (close enough -- bot can take over)")
            raise SystemExit(0)
    read = _measure(reader, grab, 3)
    if read.found and read.in_front and read.magnitude > 0.07:
        _correct(sender, read, gain=0.3, min_press=0.04, max_press=0.10,
                 deadzone=0.05)
    time.sleep(0.8)
print("ceiling reached without commit -- check the scene")
raise SystemExit(1)
