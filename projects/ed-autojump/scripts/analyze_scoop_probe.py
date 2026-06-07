"""Summarize a manual_scoop_probe jsonl: events, scoop window, rates, heat."""
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
rows = [json.loads(l) for l in path.open(encoding="utf-8")]
st = [r for r in rows if r["kind"] == "status"]
jn = [r for r in rows if r["kind"] == "journal"]
t0 = rows[0]["t"]

print("journal events:")
for r in jn:
    print(f"  t+{r['t']-t0:6.1f}s {r['event']:20s} "
          f"{r.get('StarClass') or ''} {r.get('Body') or ''} "
          f"{('scooped=%.2f total=%.2f' % (r['Scooped'], r['Total'])) if r['event']=='FuelScoop' else ''}")

sc = [r for r in st if r["scooping"]]
print(f"\nstatus rows={len(st)}  scooping rows={len(sc)}")
if sc:
    print(f"scoop window: t+{sc[0]['t']-t0:.1f}s .. t+{sc[-1]['t']-t0:.1f}s  "
          f"fuel {sc[0]['fuel']:.2f} -> {sc[-1]['fuel']:.2f}")

print("\nrate samples (t, rate t/s, frac of 0.878, fuel, heat, scooping):")
for r in st:
    if r["rate"]:
        print(f"  t+{r['t']-t0:6.1f}  {r['rate']:6.3f}  "
              f"{r['rate']/0.878:5.0%}  {r['fuel']:6.2f}  "
              f"heat={r['heat']}  scoop={r['scooping']}")

heats = [r["heat"] for r in st if r.get("heat") is not None]
print("\nheat field:", f"min={min(heats)} max={max(heats)}" if heats else "never written")
print("final fuel:", st[-1]["fuel"])
print("overheating flags:", sum(1 for r in st if r["overheating"]))
