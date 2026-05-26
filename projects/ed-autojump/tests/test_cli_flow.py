import json
from pathlib import Path

from ed_autojump.cli import main


def test_run_builds_flowrunner_and_exits_cleanly(tmp_path, monkeypatch):
    # Minimal journal dir with an empty Status.json so readers don't error.
    jdir = tmp_path / "journal"
    jdir.mkdir()
    (jdir / "Status.json").write_text(json.dumps({"Flags": 0}), encoding="utf-8")
    (jdir / "Journal.2026-05-25T000000.01.log").write_text("", encoding="utf-8")

    # Run without --engage-keys (NullSender, no vision) for ~0s and assert exit 0.
    rc = main(["run", "--journal-dir", str(jdir), "--duration", "0"])
    assert rc == 0
