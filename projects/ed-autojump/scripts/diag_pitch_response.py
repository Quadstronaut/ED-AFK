r"""Does a pitch press actually rotate the ship? Capture-press-capture diff.

2026-06-06 14:09 (run 4): 31 blind 1.0s PitchUps with zero scene change in
the compass crop -- either the keys aren't rotating the ship, or every
orientation this close to the star is equally washed. This sends ONE pitch
press and diffs full-region frames before/after.

Usage: .venv\Scripts\python scripts\diag_pitch_response.py [hold_s]
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from ed_autojump.config import load_config
from ed_autojump.keys import DirectInputSender, parse_binds
from ed_autojump.launcher.focus import focus_ed_window
from ed_autojump.vision.capture import GdiGrabber

hold = float(sys.argv[1]) if len(sys.argv) > 1 else 2.0

cfg = load_config(Path(__file__).resolve().parents[1] / "config.toml")
binds_path = Path(__file__).resolve().parents[1] / "src" / "ed_autojump" / \
    "binds" / "ED-AFK.4.2.binds"
sender = DirectInputSender(parse_binds(binds_path))
# wide view: the top-2/3 sky region tells us about gross scene change, the
# compass crop about the dash. Grab both.
sky = GdiGrabber((0, 0, 1920, 720))
compass = GdiGrabber(tuple(cfg.vision.region))

if not focus_ed_window():
    raise SystemExit("could not focus ED window -- aborting, no keys sent")
time.sleep(0.6)

sky0, comp0 = sky.grab(), compass.grab()
sender.press("PitchUpButton", hold=hold)
time.sleep(1.5)  # rotation + FA damping settle
sky1, comp1 = sky.grab(), compass.grab()

stamp = datetime.now(timezone.utc).strftime("%H%M%S")
out = Path.home() / "ed-afk-sessions" / f"diag_pitch_{stamp}"
out.mkdir(parents=True, exist_ok=True)
for name, img in [("sky0", sky0), ("sky1", sky1),
                  ("comp0", comp0), ("comp1", comp1)]:
    cv2.imwrite(str(out / f"{name}.png"), img)

sky_diff = float(np.mean(cv2.absdiff(sky0, sky1)))
comp_diff = float(np.mean(cv2.absdiff(comp0, comp1)))
print(f"pitch {hold}s -> sky mean|diff|={sky_diff:.2f}  "
      f"compass mean|diff|={comp_diff:.2f}")
print(f"(rule of thumb: <1 = nothing moved, >5 = scene clearly changed)")
print(f"frames -> {out}")
