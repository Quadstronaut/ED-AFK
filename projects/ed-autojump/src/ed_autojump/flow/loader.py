"""Load + validate procedure TOML files into `Procedure` objects.

A procedure file looks like:

    parallel = false              # optional
    stop_on_event = "..."         # optional (parallel tracks)
    timeout_s = 12.0              # optional
    parallel_tracks = ["honk"]    # optional

    [on_required_fail]            # optional
    retry_from = "sc_assist_orbit"
    max_retries = 3
    backoff_s = 2.0

    steps = [
      { action = "target_ahead" },
      { action = "wait", s = 10.0 },
      { action = "orient_compass", required = true },
    ]
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

from .model import OnRequiredFail, Procedure, Step


def _step_from_table(table: dict) -> Step:
    table = dict(table)
    action = table.pop("action")
    required = bool(table.pop("required", False))
    return Step(action=action, params=table, required=required)


def load_procedure(path: str | Path) -> Procedure:
    """Parse a single procedure TOML file. The procedure name is the filename
    stem (`arrival.toml` -> "arrival")."""
    p = Path(path)
    with open(p, "rb") as fh:
        raw = tomllib.load(fh)

    orf_table = dict(raw.get("on_required_fail", {}))
    # TOML places bare keys after [on_required_fail] inside that table, so
    # `steps` may land there instead of at root level.
    raw_steps = raw.get("steps") or orf_table.pop("steps", [])
    steps = tuple(_step_from_table(t) for t in raw_steps)

    on_required_fail = OnRequiredFail(
        retry_from=orf_table.get("retry_from"),
        max_retries=int(orf_table.get("max_retries", 0)),
        backoff_s=float(orf_table.get("backoff_s", 0.0)),
    )

    return Procedure(
        name=p.stem,
        steps=steps,
        parallel=bool(raw.get("parallel", False)),
        stop_on_event=raw.get("stop_on_event"),
        timeout_s=float(raw.get("timeout_s", 0.0)),
        parallel_tracks=tuple(raw.get("parallel_tracks", ())),
        on_required_fail=on_required_fail,
    )


def load_procedures(directory: str | Path) -> dict[str, Procedure]:
    """Load every `*.toml` in `directory`, keyed by procedure name."""
    d = Path(directory)
    return {p.stem: load_procedure(p) for p in sorted(d.glob("*.toml"))}


def validate_procedure(proc: Procedure, known_actions: Iterable[str]) -> list[str]:
    """Return a list of human-readable problems; empty list == valid."""
    known = set(known_actions)
    errors: list[str] = []
    for i, step in enumerate(proc.steps):
        if step.action not in known:
            errors.append(
                f"{proc.name}: step {i} uses unknown action {step.action!r}"
            )
    rf = proc.on_required_fail.retry_from
    if rf is not None and proc.index_of_action(rf) is None:
        errors.append(
            f"{proc.name}: on_required_fail.retry_from {rf!r} matches no step"
        )
    return errors
