"""Pass-1 manual scoop recorder (2026-06-07, scoop-refuel live calibration).

The operator flies one manual scoop; this records the ground truth the
automated step's tunables hang on:
- Status.json Fuel.FuelMain at every file change -> observed scoop rate
  (changed-samples windowed slope, the same math as step_scoop_refuel)
- ScoopingFuel / OverHeating flags, Heat field when Frontier writes it
- journal FuelScoop / ReservoirReplenished / SupercruiseExit /
  SupercruiseEntry lines as they land (SC-assist climb-out verdict)

Writes JSONL to logs/scoop_probe_<stamp>.jsonl and mirrors a 1-line status
to stdout every second so a tail of the console shows live state.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

SAVED = Path(os.path.expandvars(
    r"%USERPROFILE%\Saved Games\Frontier Developments\Elite Dangerous"))
STATUS = SAVED / "Status.json"
OUT_DIR = Path(__file__).resolve().parents[1] / "logs"
OUT_DIR.mkdir(exist_ok=True)
OUT = OUT_DIR / f"scoop_probe_{time.strftime('%Y%m%dT%H%M%S')}.jsonl"

SCOOPING = 1 << 11
OVERHEATING = 1 << 20
SUPERCRUISE = 1 << 4
MAX_RATE_6A = 0.878  # t/s, EDCD table — the standoff yardstick

JOURNAL_EVENTS = ("FuelScoop", "ReservoirReplenished", "SupercruiseExit",
                  "SupercruiseEntry", "FSDJump", "StartJump")


def newest_journal() -> Path:
    return max(SAVED.glob("Journal.*.log"), key=lambda p: p.stat().st_mtime)


def rate_from(samples: list[tuple[float, float]], now: float,
              window_s: float = 2.0) -> float | None:
    """Changed-samples windowed slope — mirror of steps._scoop_window_rate."""
    if not samples:
        return None
    recent = [s for s in samples if s[0] >= now - window_s]
    if len(recent) >= 2 and recent[-1][0] > recent[0][0]:
        return (recent[-1][1] - recent[0][1]) / (recent[-1][0] - recent[0][0])
    if samples[-1][0] < now - window_s:
        return 0.0
    return None


def main() -> None:
    print(f"[probe] recording -> {OUT}", flush=True)
    jpath = newest_journal()
    jfh = jpath.open("r", encoding="utf-8")
    jfh.seek(0, 2)  # tail from EOF: live lines only
    samples: list[tuple[float, float]] = []
    last_fuel = None
    last_status_ts = None
    last_print = 0.0
    peak_rate = 0.0
    with OUT.open("a", encoding="utf-8") as out:
        while True:
            now = time.monotonic()
            # ---- journal tail (rotation-aware) ----------------------------
            np_ = newest_journal()
            if np_ != jpath:
                jpath, jfh = np_, np_.open("r", encoding="utf-8")
            for line in jfh.readlines():
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("event") in JOURNAL_EVENTS:
                    row = {"t": now, "kind": "journal", **ev}
                    out.write(json.dumps(row) + "\n")
                    out.flush()
                    print(f"[journal] {ev.get('event')}: "
                          f"{json.dumps({k: v for k, v in ev.items() if k not in ('timestamp', 'event')})[:120]}",
                          flush=True)
            # ---- status sample --------------------------------------------
            try:
                st = json.loads(STATUS.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                time.sleep(0.25)
                continue
            ts = st.get("timestamp")
            if ts != last_status_ts:
                last_status_ts = ts
                flags = st.get("Flags", 0)
                fuel = (st.get("Fuel") or {}).get("FuelMain")
                heat = st.get("Heat")
                if fuel is not None and fuel != last_fuel:
                    samples.append((now, fuel))
                    last_fuel = fuel
                    if len(samples) > 40:
                        samples.pop(0)
                r = rate_from(samples, now)
                if r is not None and r > peak_rate:
                    peak_rate = r
                row = {"t": now, "kind": "status", "ts": ts, "fuel": fuel,
                       "reservoir": (st.get("Fuel") or {}).get("FuelReservoir"),
                       "scooping": bool(flags & SCOOPING),
                       "overheating": bool(flags & OVERHEATING),
                       "supercruise": bool(flags & SUPERCRUISE),
                       "heat": heat, "rate": r}
                out.write(json.dumps(row) + "\n")
                out.flush()
            if now - last_print >= 1.0:
                last_print = now
                r = rate_from(samples, now)
                frac = (f"{r / MAX_RATE_6A:4.0%}" if r else "   -")
                print(f"[live] fuel={last_fuel} rate="
                      f"{r if r is None else round(r, 3)} t/s ({frac} of 6A max)"
                      f" peak={round(peak_rate, 3)}", flush=True)
            time.sleep(0.2)


if __name__ == "__main__":
    main()
