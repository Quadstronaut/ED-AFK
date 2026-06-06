r"""One-shot: focus ED and press SetSpeedZero. NOTHING else.

Emergency stop for the 2026-06-06 14:10 scene: ship nose-on the star's
surface in normal space, thrusting at 50% throttle (smack_recovery's
pre-pitch set_throttle), bot stopped. Zero the throttle so the ship stops
closing while the procedure fix lands.

Usage: .venv\Scripts\python scripts\manual_throttle_zero.py
"""

from __future__ import annotations

import time
from pathlib import Path

from ed_autojump.keys import DirectInputSender, parse_binds
from ed_autojump.launcher.focus import focus_ed_window

binds_path = Path(__file__).resolve().parents[1] / "src" / "ed_autojump" / \
    "binds" / "ED-AFK.4.2.binds"
sender = DirectInputSender(parse_binds(binds_path))

if not focus_ed_window():
    raise SystemExit("could not focus ED window -- NOT pressing anything")
time.sleep(0.6)
sender.press("SetSpeedZero", hold=0.05)
print("SetSpeedZero sent")
