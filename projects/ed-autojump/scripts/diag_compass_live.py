r"""One-shot live compass diagnostic — capture-only, sends NO keys.

Forensics for the 2026-06-06 12:37 orient oscillation (session_123734): the
loop's last 8 presses were all exactly max_press, which is either saturated
reads or the behind-flip branch firing near centre (suspected in_front
misclassification). The recorder doesn't log reads, but ED is still running
with the ship parked mid-oscillation — so run the EXACT production path
(GdiGrabber region crop -> CyanDotReader) right now and dump ground truth.

Usage:  .venv\Scripts\python scripts\diag_compass_live.py [n_reads]
Output: ~/ed-afk-sessions/diag_<stamp>/ -- crops as PNG + reads.jsonl
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2

from ed_autojump.config import load_config
from ed_autojump.launcher.focus import focus_ed_window
from ed_autojump.vision.capture import GdiGrabber
from ed_autojump.vision.cyan_reader import CyanDotReader

N = int(sys.argv[1]) if len(sys.argv) > 1 else 20

cfg = load_config(Path(__file__).resolve().parents[1] / "config.toml")
region = tuple(cfg.vision.region)
print(f"region={region}  backend={cfg.vision.capture_backend}")

if not focus_ed_window():
    print("WARN: could not focus ED window -- crops may show the wrong content")
time.sleep(0.5)  # let the foreground transition finish before the first grab

grabber = GdiGrabber(region)
reader = CyanDotReader(radius=cfg.vision.compass_radius, use_ring_detect=True)

stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
out_dir = Path.home() / "ed-afk-sessions" / f"diag_{stamp}"
out_dir.mkdir(parents=True, exist_ok=True)

rows = []
for i in range(N):
    frame = grabber.grab()
    r = reader.read(frame)
    row = {
        "i": i,
        "found": r.found,
        "in_front": r.in_front,
        "ox": round(r.offset_x, 4),
        "oy": round(r.offset_y, 4),
        "mag": round(r.magnitude, 4),
    }
    rows.append(row)
    cv2.imwrite(str(out_dir / f"read_{i:02d}.png"), frame)
    print(row)
    time.sleep(0.15)  # ~7 reads/s, same ballpark as _measure's burst pacing

with (out_dir / "reads.jsonl").open("w", encoding="utf-8") as fp:
    for row in rows:
        fp.write(json.dumps(row) + "\n")

n_found = sum(r["found"] for r in rows)
n_front = sum(r["in_front"] for r in rows if r["found"])
print(f"\n{N} reads -> found={n_found}  in_front={n_front}/{n_found}")
print(f"crops + reads.jsonl -> {out_dir}")
