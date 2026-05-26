import textwrap
from pathlib import Path

import pytest

from ed_autojump.flow.loader import (
    load_procedure,
    load_procedures,
    validate_procedure,
)


def _write(p: Path, body: str) -> Path:
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_load_procedure_parses_steps_and_policy(tmp_path):
    f = _write(tmp_path / "arrival.toml", """
        parallel_tracks = ["honk"]

        [on_required_fail]
        retry_from = "sc_assist_orbit"
        max_retries = 3
        backoff_s = 2.0

        steps = [
          { action = "target_ahead" },
          { action = "wait", s = 10.0 },
          { action = "orient_compass", required = true },
        ]
    """)
    proc = load_procedure(f)
    assert proc.name == "arrival"
    assert proc.parallel_tracks == ("honk",)
    assert proc.on_required_fail.retry_from == "sc_assist_orbit"
    assert proc.on_required_fail.max_retries == 3
    assert len(proc.steps) == 3
    # params exclude action + required
    assert proc.steps[1].action == "wait"
    assert proc.steps[1].params == {"s": 10.0}
    assert proc.steps[2].required is True
    assert proc.steps[2].params == {}


def test_load_parallel_track(tmp_path):
    f = _write(tmp_path / "honk.toml", """
        parallel = true
        stop_on_event = "FSSDiscoveryScan"
        timeout_s = 12.0
        steps = [ { action = "press", bind = "ExplorationFSSDiscoveryScan", hold_s = 6.0 } ]
    """)
    proc = load_procedure(f)
    assert proc.parallel is True
    assert proc.stop_on_event == "FSSDiscoveryScan"
    assert proc.timeout_s == 12.0


def test_validate_flags_unknown_action_and_bad_retry(tmp_path):
    f = _write(tmp_path / "bad.toml", """
        [on_required_fail]
        retry_from = "nonexistent_step"
        steps = [ { action = "no_such_action" } ]
    """)
    proc = load_procedure(f)
    errors = validate_procedure(proc, known_actions={"wait", "press"})
    assert any("no_such_action" in e for e in errors)
    assert any("nonexistent_step" in e for e in errors)


def test_load_procedures_reads_a_directory(tmp_path):
    _write(tmp_path / "a.toml", 'steps = [ { action = "wait", s = 1.0 } ]')
    _write(tmp_path / "b.toml", 'steps = [ { action = "wait", s = 2.0 } ]')
    procs = load_procedures(tmp_path)
    assert set(procs.keys()) == {"a", "b"}
