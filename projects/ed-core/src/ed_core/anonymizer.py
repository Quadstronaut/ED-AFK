"""
Session anonymizer.

Reads a session JSONL written by `ed_autojump.recorder.Recorder`, emits a
scrubbed copy. Scrub policy is per user scoping: replace
**Commander**, **FID**, and **AccountID** anywhere they appear in a
journal payload. Leave system names, timestamps, ship loadouts, and FSM
transitions untouched — those are public game data and load-bearing for
fuel/heat analysis on recorded sessions.

Usage:
    python -m ed_autojump.anonymizer in.jsonl out.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


# Field names we always replace, regardless of nesting depth. Keys are
# case-sensitive — Frontier's journal is consistent PascalCase.
_SCRUB_STR = {
    "Commander": "AnonCmdr",
    "FID": "F0",
}
_SCRUB_INT = {
    "AccountID": 0,
}


def anonymize_obj(obj: Any) -> Any:
    """Return a scrubbed deep copy. Does not mutate `obj`."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k in _SCRUB_STR:
                out[k] = _SCRUB_STR[k]
            elif k in _SCRUB_INT:
                out[k] = _SCRUB_INT[k]
            else:
                out[k] = anonymize_obj(v)
        return out
    if isinstance(obj, list):
        return [anonymize_obj(item) for item in obj]
    return obj


def anonymize_jsonl(src: Path, dst: Path) -> int:
    """Read JSONL at `src`, write scrubbed JSONL at `dst`.

    Returns the number of lines processed.
    """
    n = 0
    with src.open("r", encoding="utf-8") as fin, dst.open("w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            scrubbed = anonymize_obj(row)
            json.dump(scrubbed, fout, ensure_ascii=False)
            fout.write("\n")
            n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="ed_autojump.anonymizer",
        description="Scrub CMDR / FID / AccountID from a session JSONL.",
    )
    p.add_argument("input", type=Path)
    p.add_argument("output", type=Path)
    args = p.parse_args(argv)
    n = anonymize_jsonl(args.input, args.output)
    print(f"anonymized {n} rows -> {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
