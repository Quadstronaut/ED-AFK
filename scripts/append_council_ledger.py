"""One-shot: append a council's ledger_entries + unresolved_dissent (verbatim,
stamped) to the repo ledger. JSONL, one object per line. argv: <output.json>
<ledger.jsonl> <run_id> <council_name>."""
import json
import sys
from pathlib import Path

OUT, LEDGER, RUN, COUNCIL = (Path(sys.argv[1]), Path(sys.argv[2]),
                             sys.argv[3], sys.argv[4])
TS = "2026-06-15"

data = json.loads(OUT.read_text(encoding="utf-8"))
arb = data["result"]["arbitration"]
dec = arb.get("decision")
rows = []
for e in arb.get("ledger_entries", []):
    rows.append({"ts": TS, "council": COUNCIL, "run_id": RUN,
                 "decision": dec, "kind": "ledger", "text": e})
for d in arb.get("unresolved_dissent", []):
    rows.append({"ts": TS, "council": COUNCIL, "run_id": RUN,
                 "decision": dec, "kind": "dissent", "text": d})
with LEDGER.open("a", encoding="utf-8") as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
print(f"Appended {len(rows)} entries (decision={dec}) to {LEDGER}")
