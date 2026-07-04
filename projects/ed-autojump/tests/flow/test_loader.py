import textwrap
from pathlib import Path

import pytest

from ed_core.flow.loader import (
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


def test_load_procedure_parses_retry_anchor(tmp_path):
    """retry_anchor is a Step field like required — stripped from params."""
    f = _write(tmp_path / "startup.toml", """
        steps = [
          { action = "target_ahead" },
          { action = "wait", s = 13.0, retry_anchor = true },
          { action = "orient_compass", required = true },
        ]
    """)
    proc = load_procedure(f)
    assert proc.steps[0].retry_anchor is False
    assert proc.steps[1].retry_anchor is True
    assert proc.steps[1].params == {"s": 13.0}   # not leaked into params


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


def test_load_procedure_parses_retry_from_if_supercruise(tmp_path):
    """State-aware retry override (operator-dictated, 2026-06-07): parsed off
    the on_required_fail table like retry_from."""
    f = _write(tmp_path / "smack_recovery.toml", """
        [on_required_fail]
        retry_from = "set_throttle"
        retry_from_if_supercruise = "target_next_route"
        max_retries = 3

        steps = [
          { action = "set_throttle", pct = 0 },
          { action = "orient_compass", required = true },
          { action = "target_next_route", required = true },
        ]
    """)
    proc = load_procedure(f)
    assert proc.on_required_fail.retry_from == "set_throttle"
    assert proc.on_required_fail.retry_from_if_supercruise == "target_next_route"


def test_load_procedure_defaults_retry_from_if_supercruise_none(tmp_path):
    """Procedures without the key leave it None (arrival/startup must never
    trigger the SC branch / status read)."""
    f = _write(tmp_path / "arrival.toml", """
        [on_required_fail]
        retry_from = "x"
        steps = [ { action = "x" } ]
    """)
    assert load_procedure(f).on_required_fail.retry_from_if_supercruise is None


def test_validate_flags_unknown_retry_from_if_supercruise(tmp_path):
    """A typo in retry_from_if_supercruise must fail validation loudly — left
    silent it would fall through to retry_from and re-burn the real-space lane."""
    f = _write(tmp_path / "bad.toml", """
        [on_required_fail]
        retry_from_if_supercruise = "no_such_step"
        steps = [ { action = "wait", s = 1.0 } ]
    """)
    proc = load_procedure(f)
    errors = validate_procedure(proc, known_actions={"wait"})
    assert any("retry_from_if_supercruise" in e and "no_such_step" in e
               for e in errors)


def test_load_procedures_reads_a_directory(tmp_path):
    _write(tmp_path / "a.toml", 'steps = [ { action = "wait", s = 1.0 } ]')
    _write(tmp_path / "b.toml", 'steps = [ { action = "wait", s = 2.0 } ]')
    procs = load_procedures(tmp_path)
    assert set(procs.keys()) == {"a", "b"}


# ---- loop primitive: loop_to + loop_max (council #4) -----------------------

def test_load_procedure_parses_loop_to_and_loop_max(tmp_path):
    """loop_to + loop_max are Step fields like skip_to — stripped from params,
    loop_max defaults to 64 when absent."""
    f = _write(tmp_path / "exploration.toml", """
        steps = [
          { action = "head" },
          { action = "back", pct = 0, loop_to = "head", loop_max = 12 },
          { action = "tail" },
        ]
    """)
    proc = load_procedure(f)
    assert proc.steps[0].loop_to is None
    assert proc.steps[0].loop_max == 64          # default when unset
    back = proc.steps[1]
    assert back.loop_to == "head"
    assert back.loop_max == 12
    assert back.params == {"pct": 0}             # loop_to/loop_max not leaked


def test_validate_flags_unresolvable_loop_to(tmp_path):
    """A loop_to naming no step fails validation loudly (same as skip_to) — left
    silent it would degrade to a one-step advance and never loop."""
    f = _write(tmp_path / "bad.toml", """
        steps = [
          { action = "head" },
          { action = "back", loop_to = "nowhere" },
        ]
    """)
    proc = load_procedure(f)
    errors = validate_procedure(proc, known_actions={"head", "back"})
    assert any("loop_to" in e and "nowhere" in e for e in errors)
