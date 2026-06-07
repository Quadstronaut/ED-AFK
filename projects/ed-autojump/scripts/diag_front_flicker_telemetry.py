r"""DIAGNOSTIC (2026-06-06): quantify the filled/hollow boundary disease from
session telemetry. Iteration rows carry `raw` per-sample reads
[found, in_front, ox, oy]; samples within one iteration are taken back-to-back
at one ship attitude, so any in_front DISAGREEMENT inside an iteration is
classifier noise, not ship motion.

Usage: .venv\Scripts\python scripts\diag_front_flicker_telemetry.py [session.jsonl ...]
(no args = every session_2026-06-06*.jsonl in ~/ed-afk-sessions)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

paths = ([Path(a) for a in sys.argv[1:]] or
         sorted(Path.home().glob("ed-afk-sessions/session_2026-06-06*.jsonl")))

total_iters = 0          # iterations with raw samples and a found majority
mixed_iters = 0          # ... where found samples disagree on in_front
median_flips = 0         # consecutive-iteration median in_front flips at stable offset
examples = []

for p in paths:
    prev = None  # (in_front, ox, oy) of previous iteration in the same step run
    for line in p.open(encoding="utf-8"):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        pay = row.get("payload") or {}
        raw = pay.get("raw")
        if not raw:
            prev = None if row.get("outcome_type") == "Step" else prev
            continue
        found = [s for s in raw if s[0]]
        if len(found) <= len(raw) // 2:
            continue
        total_iters += 1
        fronts = sum(1 for s in found if s[1])
        if 0 < fronts < len(found):
            mixed_iters += 1
            if len(examples) < 12:
                examples.append((p.name, row.get("ts"), pay.get("i"),
                                 f"{fronts}/{len(found)} front",
                                 f"mag={pay.get('mag')}"))
        # median-level flip at stable position (offset moved < 0.08)
        cur = (bool(pay.get("in_front")), pay.get("ox", 0.0), pay.get("oy", 0.0))
        if prev is not None:
            dist = ((cur[1] - prev[1]) ** 2 + (cur[2] - prev[2]) ** 2) ** 0.5
            if cur[0] != prev[0] and dist < 0.08:
                median_flips += 1
        prev = cur

print(f"iterations with raw samples + found majority: {total_iters}")
print(f"  intra-iteration in_front DISAGREEMENT:      {mixed_iters} "
      f"({100.0 * mixed_iters / max(1, total_iters):.1f}%)")
print(f"  median in_front flips at stable position:   {median_flips}")
print("\nexamples (session, ts, iter, vote, magnitude):")
for e in examples:
    print(f"  {e}")
