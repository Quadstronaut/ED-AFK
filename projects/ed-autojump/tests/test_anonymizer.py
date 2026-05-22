"""Anonymizer scrubs CMDR / FID / AccountID from session JSONL.

Per the user's explicit scope: scrub commander name + account IDs,
KEEP system names + timestamps (system data is public game state, and
timestamps are load-bearing for fuel/heat rate analysis).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ed_autojump.anonymizer import anonymize_jsonl, anonymize_obj


def _write(p: Path, rows: list[dict]) -> None:
    with p.open("w", encoding="utf-8") as f:
        for r in rows:
            json.dump(r, f, ensure_ascii=False)
            f.write("\n")


def _read(p: Path) -> list[dict]:
    return [json.loads(L) for L in p.read_text(encoding="utf-8").splitlines() if L.strip()]


def test_scrubs_commander_name_in_journal_payload(tmp_path: Path):
    inp = tmp_path / "in.jsonl"
    out = tmp_path / "out.jsonl"
    _write(inp, [{
        "kind": "journal", "event_name": "Commander",
        "payload": {"Commander": "RealCmdrName", "FID": "F1234567"},
    }])
    anonymize_jsonl(inp, out)
    rows = _read(out)
    assert rows[0]["payload"]["Commander"] == "AnonCmdr"
    assert rows[0]["payload"]["FID"] == "F0"


def test_scrubs_account_id_and_fid_anywhere(tmp_path: Path):
    inp = tmp_path / "in.jsonl"
    out = tmp_path / "out.jsonl"
    _write(inp, [{
        "kind": "journal", "event_name": "LoadGame",
        "payload": {"FID": "F9876543", "AccountID": 12345678, "Commander": "X"},
    }])
    anonymize_jsonl(inp, out)
    p = _read(out)[0]["payload"]
    assert p["FID"] == "F0"
    assert p["AccountID"] == 0
    assert p["Commander"] == "AnonCmdr"


def test_keeps_system_names_and_timestamps(tmp_path: Path):
    inp = tmp_path / "in.jsonl"
    out = tmp_path / "out.jsonl"
    _write(inp, [{
        "ts": "2026-05-22T12:00:00.123Z",
        "kind": "journal", "event_name": "FSDJump",
        "payload": {
            "StarSystem": "HIP 12345",
            "SystemAddress": 56789012345,
            "timestamp": "2026-05-22T12:00:01Z",
        },
    }])
    anonymize_jsonl(inp, out)
    row = _read(out)[0]
    assert row["ts"] == "2026-05-22T12:00:00.123Z"
    assert row["payload"]["StarSystem"] == "HIP 12345"
    assert row["payload"]["timestamp"] == "2026-05-22T12:00:01Z"


def test_scrubs_recursively_in_nested_lists(tmp_path: Path):
    inp = tmp_path / "in.jsonl"
    out = tmp_path / "out.jsonl"
    _write(inp, [{
        "kind": "journal", "event_name": "Materials",
        "payload": {
            "Raw": [{"Commander": "Foo", "Count": 3}, {"Name": "Iron"}],
        },
    }])
    anonymize_jsonl(inp, out)
    raw = _read(out)[0]["payload"]["Raw"]
    assert raw[0]["Commander"] == "AnonCmdr"
    assert raw[0]["Count"] == 3
    assert raw[1] == {"Name": "Iron"}


def test_non_journal_rows_pass_through_unchanged(tmp_path: Path):
    inp = tmp_path / "in.jsonl"
    out = tmp_path / "out.jsonl"
    _write(inp, [
        {"kind": "fsm", "from": "READY", "to": "CHARGING"},
        {"kind": "action", "action": "PitchUpButton", "hold_s": 2.0},
    ])
    anonymize_jsonl(inp, out)
    rows = _read(out)
    assert rows[0] == {"kind": "fsm", "from": "READY", "to": "CHARGING"}
    assert rows[1] == {"kind": "action", "action": "PitchUpButton", "hold_s": 2.0}


def test_anonymize_obj_does_not_mutate_input():
    inp = {"Commander": "Real", "Inner": {"FID": "F123"}}
    out = anonymize_obj(inp)
    assert inp["Commander"] == "Real"
    assert inp["Inner"]["FID"] == "F123"
    assert out["Commander"] == "AnonCmdr"
    assert out["Inner"]["FID"] == "F0"


def test_cli_module_entrypoint(tmp_path: Path):
    """`python -m ed_autojump.anonymizer <in> <out>` should produce a
    scrubbed copy. We import the main() rather than spawning a subprocess
    so we get deterministic exit codes."""
    inp = tmp_path / "in.jsonl"
    out = tmp_path / "out.jsonl"
    _write(inp, [{
        "kind": "journal", "event_name": "Commander",
        "payload": {"Commander": "X", "FID": "F1"},
    }])
    from ed_autojump.anonymizer import main as anon_main
    rc = anon_main([str(inp), str(out)])
    assert rc == 0
    assert _read(out)[0]["payload"]["Commander"] == "AnonCmdr"
