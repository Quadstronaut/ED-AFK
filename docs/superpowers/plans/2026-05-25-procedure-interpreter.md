# Procedure Interpreter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace ed-autojump's orchestrator escape/engage tangle with a small step library + interpreter that runs human-editable per-procedure TOML files, so the bot stops ramming the star and the jump fails closed.

**Architecture:** A `flow/` package holds the engine (typed `Step`/`Procedure` model, a TOML loader+validator, a step library of one tested function per primitive, a sequential interpreter with retry-from/abort semantics, and a `FlowRunner` dispatcher that maps live journal events to procedures). Procedure *data* lives in `projects/ed-autojump/procedures/*.toml`. The CLI builds a `StepContext` from config + the existing Sender/vision and runs the `FlowRunner` live loop.

**Tech Stack:** Python 3.11, `tomllib` (stdlib), `pydantic` (existing event/status models), `pytest` (`pythonpath=["src"]`). Reuses `keys.sender.Sender`, `executor.align.align_to_target`/`_measure`, `executor.navpanel.engage_supercruise_assist`, `vision.compass.CompassRead`, `vision.capture.build_vision`, `journal`/`status` readers.

---

## File structure

Created:
- `src/ed_autojump/flow/__init__.py` — package marker + public exports
- `src/ed_autojump/flow/model.py` — `Step`, `OnRequiredFail`, `Procedure` dataclasses
- `src/ed_autojump/flow/loader.py` — TOML → `Procedure`; directory load; validation
- `src/ed_autojump/flow/context.py` — `StepContext` (deps injected into steps)
- `src/ed_autojump/flow/steps.py` — step primitives + `STEP_REGISTRY`
- `src/ed_autojump/flow/interpreter.py` — `run_procedure`, `StepResult`, `ProcedureResult`
- `src/ed_autojump/flow/dispatcher.py` — `FlowRunner` (event→procedure, parallel honk, live loop)
- `projects/ed-autojump/procedures/honk.toml`
- `projects/ed-autojump/procedures/arrival.toml`
- `projects/ed-autojump/procedures/startup.toml`
- `projects/ed-autojump/procedures/smack_recovery.toml`
- `src/ed_autojump/archive/brightness/` — archived sun-avoid code (kept, unwired)
- Tests under `tests/flow/`

Modified:
- `src/ed_autojump/cli.py` — `cmd_run` builds + runs `FlowRunner` instead of `Orchestrator`
- `config.toml` + `src/ed_autojump/config.py` — fix `[cv].target_resolution`; drop `[escape]`
- Retire `orchestrator.py` escape/engage methods; delete dead-path modules per §7 of the spec

Spec reference: `docs/superpowers/specs/2026-05-25-procedure-interpreter-design.md`.

---

## Task 1: Procedure model

**Files:**
- Create: `src/ed_autojump/flow/__init__.py`
- Create: `src/ed_autojump/flow/model.py`
- Test: `tests/flow/__init__.py`, `tests/flow/test_model.py`

- [ ] **Step 1: Create the package markers**

Create `src/ed_autojump/flow/__init__.py`:
```python
"""Procedure-flow engine: model, loader, steps, interpreter, dispatcher."""
```
Create `tests/flow/__init__.py` with a shared test double (the real
`RecordingSender` requires a `binds` argument; this fake is dependency-free and
records pressed actions). Every flow test imports `FakeSender` from here:
```python
"""Flow tests package + shared test double."""


class FakeSender:
    """Minimal Sender stand-in: records pressed actions; raises KeyError for any
    action listed in `unbound` (to exercise the steps' fail-on-missing-bind path)."""

    def __init__(self, unbound=()):
        self.events: list[str] = []
        self._unbound = set(unbound)

    def press(self, action, *, hold=0.05):
        if action in self._unbound:
            raise KeyError(action)
        self.events.append(action)

    def actions(self):
        return list(self.events)
```

> Run pytest from `projects/ed-autojump/` so `from tests.flow import FakeSender`
> resolves (the `tests` and `tests/flow` packages have `__init__.py`).

- [ ] **Step 2: Write the failing test**

`tests/flow/test_model.py`:
```python
from ed_autojump.flow.model import Step, OnRequiredFail, Procedure


def test_step_holds_action_and_params():
    s = Step(action="press", params={"bind": "X", "hold_s": 6.0}, required=True)
    assert s.action == "press"
    assert s.params["bind"] == "X"
    assert s.required is True


def test_procedure_defaults():
    proc = Procedure(name="arrival", steps=(Step(action="wait", params={"s": 1.0}),))
    assert proc.parallel is False
    assert proc.stop_on_event is None
    assert proc.parallel_tracks == ()
    assert proc.on_required_fail == OnRequiredFail()


def test_index_of_action_finds_first_match():
    proc = Procedure(
        name="p",
        steps=(
            Step(action="target_ahead"),
            Step(action="orient_compass", required=True),
            Step(action="orient_compass"),
        ),
    )
    assert proc.index_of_action("orient_compass") == 1
    assert proc.index_of_action("missing") is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/flow/test_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ed_autojump.flow.model'`

- [ ] **Step 4: Implement the model**

`src/ed_autojump/flow/model.py`:
```python
"""Typed, immutable representation of a procedure and its steps."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class Step:
    """One action in a procedure. `params` is everything from the TOML inline
    table except `action` and `required`."""
    action: str
    params: dict[str, Any] = field(default_factory=dict)
    required: bool = False


@dataclass(frozen=True)
class OnRequiredFail:
    """What to do when a `required` step fails. Default = abort immediately."""
    retry_from: Optional[str] = None   # action name to resume from
    max_retries: int = 0
    backoff_s: float = 0.0


@dataclass(frozen=True)
class Procedure:
    name: str
    steps: tuple[Step, ...]
    parallel: bool = False                 # this procedure is a background track
    stop_on_event: Optional[str] = None    # journal event that ends a parallel track
    timeout_s: float = 0.0                 # hard cap for a parallel track (0 = none)
    parallel_tracks: tuple[str, ...] = ()  # procedures to launch concurrently at start
    on_required_fail: OnRequiredFail = OnRequiredFail()

    def index_of_action(self, action: str) -> Optional[int]:
        """Index of the FIRST step whose action == `action`, else None."""
        for i, s in enumerate(self.steps):
            if s.action == action:
                return i
        return None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/flow/test_model.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add src/ed_autojump/flow/__init__.py src/ed_autojump/flow/model.py tests/flow/
git commit -m "feat(flow): procedure + step model"
```

---

## Task 2: TOML loader + validator

**Files:**
- Create: `src/ed_autojump/flow/loader.py`
- Test: `tests/flow/test_loader.py`

- [ ] **Step 1: Write the failing test**

`tests/flow/test_loader.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/flow/test_loader.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ed_autojump.flow.loader'`

- [ ] **Step 3: Implement the loader**

`src/ed_autojump/flow/loader.py`:
```python
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

    steps = tuple(_step_from_table(t) for t in raw.get("steps", []))

    orf_table = raw.get("on_required_fail", {})
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/flow/test_loader.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ed_autojump/flow/loader.py tests/flow/test_loader.py
git commit -m "feat(flow): TOML procedure loader + validator"
```

---

## Task 3: StepContext

**Files:**
- Create: `src/ed_autojump/flow/context.py`
- Test: `tests/flow/test_context.py`

`StepContext` is a plain dependency bag; the test just pins its shape and defaults so later tasks can rely on the field names.

- [ ] **Step 1: Write the failing test**

`tests/flow/test_context.py`:
```python
from ed_autojump.flow.context import StepContext


def test_context_minimal_construction():
    ctx = StepContext(sender=object())
    # safe no-op defaults so steps can call them unconditionally
    assert ctx.status_supplier() is None
    assert ctx.event_time("drop") is None
    assert ctx.compass_reader is None
    assert ctx.frame_grabber is None
    assert ctx.compass_samples == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/flow/test_context.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ed_autojump.flow.context'`

- [ ] **Step 3: Implement the context**

`src/ed_autojump/flow/context.py`:
```python
"""Everything a step function may need, injected (so steps are unit-testable
with fakes and no real game / no real sleeps)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class StepContext:
    sender: Any
    clock: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep

    # vision (None when vision is off -> compass steps fail closed)
    compass_reader: Optional[Any] = None
    frame_grabber: Optional[Callable[[], Any]] = None
    align_kwargs: dict = field(default_factory=dict)
    compass_samples: int = 7

    # live state suppliers, wired by the FlowRunner
    status_supplier: Callable[[], Optional[Any]] = lambda: None
    event_time: Callable[[str], Optional[float]] = lambda name: None
    # block until `event` is logged or `timeout_s` elapses; True if seen.
    event_waiter: Optional[Callable[[str, float], bool]] = None

    # outcome logging (recorder.record_outcome), optional
    record: Optional[Callable[[str, Any], None]] = None

    def log(self, outcome_type: str, payload: Any) -> None:
        if self.record is not None:
            self.record(outcome_type, payload)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/flow/test_context.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ed_autojump/flow/context.py tests/flow/test_context.py
git commit -m "feat(flow): StepContext dependency bag"
```

---

## Task 4a: Simple steps (press, wait, set_throttle, pitch) + registry

**Files:**
- Create: `src/ed_autojump/flow/steps.py`
- Test: `tests/flow/test_steps_simple.py`

These steps only touch `sender` and `sleeper`. We use the existing
`FakeSender` (records `.actions()`) as the test double.

- [ ] **Step 1: Write the failing test**

`tests/flow/test_steps_simple.py`:
```python
from ed_autojump.flow.context import StepContext
from ed_autojump.flow.steps import STEP_REGISTRY
from tests.flow import FakeSender


def _ctx():
    sleeps = []
    sender = FakeSender()
    ctx = StepContext(sender=sender, sleeper=lambda s: sleeps.append(s))
    return ctx, sender, sleeps


def test_press_emits_the_bound_action():
    ctx, sender, _ = _ctx()
    assert STEP_REGISTRY["press"](ctx, bind="ExplorationFSSDiscoveryScan", hold_s=6.0) is True
    assert sender.actions() == ["ExplorationFSSDiscoveryScan"]


def test_wait_sleeps_the_requested_seconds():
    ctx, _, sleeps = _ctx()
    assert STEP_REGISTRY["wait"](ctx, s=10.0) is True
    assert sleeps == [10.0]


def test_set_throttle_maps_pct_to_action():
    ctx, sender, _ = _ctx()
    STEP_REGISTRY["set_throttle"](ctx, pct=0)
    STEP_REGISTRY["set_throttle"](ctx, pct=100)
    assert sender.actions() == ["SetSpeedZero", "SetSpeed100"]


def test_pitch_up_presses_pitch_up_button():
    ctx, sender, _ = _ctx()
    STEP_REGISTRY["pitch"](ctx, dir="up", hold_s=4.0)
    assert sender.actions() == ["PitchUpButton"]


def test_press_returns_false_on_unbound_action():
    sender = FakeSender(unbound={"TotallyUnbound"})
    ctx = StepContext(sender=sender, sleeper=lambda s: None)
    assert STEP_REGISTRY["press"](ctx, bind="TotallyUnbound") is False
```

> `FakeSender` records every action by default; pass `unbound={...}` to make it
> raise `KeyError` for chosen actions. Step 3 must confirm the steps catch that
> `KeyError` and return `False` rather than crash the procedure.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/flow/test_steps_simple.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ed_autojump.flow.steps'`

- [ ] **Step 3: Implement the simple steps + registry**

`src/ed_autojump/flow/steps.py` (start the file):
```python
"""Step primitives. One function per action: `step_fn(ctx, **params) -> bool`.

Every step returns True on success, False on failure. A False on a `required`
step triggers the procedure's on_required_fail policy in the interpreter; a
False never throttles or jumps. Steps catch `KeyError` from the sender (an
unbound action) and report it as a clean failure.
"""

from __future__ import annotations

from typing import Any, Callable

from .context import StepContext

# Map a throttle percentage to its ED action name.
_THROTTLE_ACTION = {
    0: "SetSpeedZero",
    25: "SetSpeed25",
    50: "SetSpeed50",
    75: "SetSpeed75",
    100: "SetSpeed100",
}


def _press(ctx: StepContext, action: str, hold_s: float = 0.05) -> bool:
    try:
        ctx.sender.press(action, hold=hold_s)
        return True
    except KeyError:
        ctx.log("BindMissing", {"action": action})
        return False


def step_press(ctx: StepContext, *, bind: str, hold_s: float = 0.05) -> bool:
    return _press(ctx, bind, hold_s)


def step_wait(ctx: StepContext, *, s: float) -> bool:
    ctx.sleeper(s)
    return True


def step_set_throttle(ctx: StepContext, *, pct: int) -> bool:
    action = _THROTTLE_ACTION.get(int(pct))
    if action is None:
        ctx.log("BadThrottle", {"pct": pct})
        return False
    return _press(ctx, action)


def step_pitch(ctx: StepContext, *, dir: str, hold_s: float) -> bool:
    action = "PitchUpButton" if dir == "up" else "PitchDownButton"
    return _press(ctx, action, hold_s)


STEP_REGISTRY: dict[str, Callable[..., bool]] = {
    "press": step_press,
    "wait": step_wait,
    "set_throttle": step_set_throttle,
    "pitch": step_pitch,
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/flow/test_steps_simple.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ed_autojump/flow/steps.py tests/flow/test_steps_simple.py
git commit -m "feat(flow): simple steps (press/wait/set_throttle/pitch) + registry"
```

---

## Task 4b: Targeting + engage steps

**Files:**
- Modify: `src/ed_autojump/flow/steps.py`
- Test: `tests/flow/test_steps_engage.py`

`engage_jump` re-checks Status flags before pressing (defense-in-depth);
`engage_supercruise` short-circuits if already in SC, else waits for the
`SupercruiseEntry` journal event.

- [ ] **Step 1: Write the failing test**

`tests/flow/test_steps_engage.py`:
```python
from types import SimpleNamespace

from ed_autojump.flow.context import StepContext
from ed_autojump.flow.steps import STEP_REGISTRY
from tests.flow import FakeSender


def _status(**flags):
    base = dict(docked=False, fsd_charging=False, fsd_cooldown=False,
                fsd_mass_locked=False, overheating=False, in_supercruise=False)
    base.update(flags)
    return SimpleNamespace(**base)


def test_target_ahead_and_next_route():
    sender = FakeSender()
    ctx = StepContext(sender=sender)
    STEP_REGISTRY["target_ahead"](ctx)
    STEP_REGISTRY["target_next_route"](ctx)
    assert sender.actions() == ["SelectTarget", "TargetNextRouteSystem"]


def test_engage_jump_throttles_then_jumps_when_clear():
    sender = FakeSender()
    ctx = StepContext(sender=sender, status_supplier=lambda: _status())
    assert STEP_REGISTRY["engage_jump"](ctx) is True
    assert sender.actions() == ["SetSpeed100", "HyperSuperCombination"]


def test_engage_jump_refuses_when_flag_blocks():
    sender = FakeSender()
    ctx = StepContext(sender=sender, status_supplier=lambda: _status(fsd_cooldown=True))
    assert STEP_REGISTRY["engage_jump"](ctx) is False
    assert sender.actions() == []   # never throttled


def test_engage_supercruise_shortcircuits_when_already_in_sc():
    sender = FakeSender()
    ctx = StepContext(sender=sender, status_supplier=lambda: _status(in_supercruise=True))
    assert STEP_REGISTRY["engage_supercruise"](ctx, timeout_s=5.0) is True
    assert sender.actions() == []   # nothing to engage


def test_engage_supercruise_presses_then_waits_for_entry():
    sender = FakeSender()
    seen = {"SupercruiseEntry": True}
    ctx = StepContext(
        sender=sender,
        status_supplier=lambda: _status(),
        event_waiter=lambda ev, t: seen.get(ev, False),
    )
    assert STEP_REGISTRY["engage_supercruise"](ctx, timeout_s=5.0) is True
    assert sender.actions() == ["Supercruise"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/flow/test_steps_engage.py -v`
Expected: FAIL with `KeyError: 'target_ahead'` (registry lacks these yet)

- [ ] **Step 3: Add the steps**

Append to `src/ed_autojump/flow/steps.py` (before `STEP_REGISTRY`, then add entries):
```python
def step_target_ahead(ctx: StepContext) -> bool:
    # SelectTarget locks the body ahead; with NOTHING ahead it clears the target.
    return _press(ctx, "SelectTarget")


def step_target_next_route(ctx: StepContext) -> bool:
    # Cancels Supercruise Assist AND locks the next route star in one press.
    return _press(ctx, "TargetNextRouteSystem")


def step_engage_jump(ctx: StepContext) -> bool:
    st = ctx.status_supplier()
    if st is not None and (
        getattr(st, "docked", False)
        or getattr(st, "fsd_charging", False)
        or getattr(st, "fsd_cooldown", False)
        or getattr(st, "fsd_mass_locked", False)
        or getattr(st, "overheating", False)
    ):
        ctx.log("EngageBlocked", {"reason": "status_flag"})
        return False
    if not _press(ctx, "SetSpeed100"):
        return False
    return _press(ctx, "HyperSuperCombination")


def step_engage_supercruise(ctx: StepContext, *, timeout_s: float = 30.0) -> bool:
    st = ctx.status_supplier()
    if st is not None and getattr(st, "in_supercruise", False):
        return True  # already in SC; nothing to engage
    if not _press(ctx, "Supercruise"):
        return False
    if ctx.event_waiter is None:
        return True  # no journal wiring (unit tests) -> proceed
    return ctx.event_waiter("SupercruiseEntry", timeout_s)
```
Then extend the registry dict:
```python
STEP_REGISTRY.update({
    "target_ahead": step_target_ahead,
    "target_next_route": step_target_next_route,
    "engage_jump": step_engage_jump,
    "engage_supercruise": step_engage_supercruise,
})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/flow/test_steps_engage.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ed_autojump/flow/steps.py tests/flow/test_steps_engage.py
git commit -m "feat(flow): targeting + fail-closed engage steps"
```

---

## Task 4c: Event-waiting steps (wait_for_event, wait_cooldown)

**Files:**
- Modify: `src/ed_autojump/flow/steps.py`
- Test: `tests/flow/test_steps_events.py`

- [ ] **Step 1: Write the failing test**

`tests/flow/test_steps_events.py`:
```python
from ed_autojump.flow.context import StepContext
from ed_autojump.flow.steps import STEP_REGISTRY
from tests.flow import FakeSender


def test_wait_for_event_delegates_to_waiter():
    ctx = StepContext(sender=FakeSender(),
                      event_waiter=lambda ev, t: ev == "SupercruiseEntry")
    assert STEP_REGISTRY["wait_for_event"](ctx, event="SupercruiseEntry", timeout_s=5.0) is True
    assert STEP_REGISTRY["wait_for_event"](ctx, event="Nope", timeout_s=5.0) is False


def test_wait_cooldown_waits_only_the_remainder():
    sleeps = []
    now = [100.0]                       # drop happened at t=100
    ctx = StepContext(
        sender=FakeSender(),
        clock=lambda: now[0],
        sleeper=lambda s: sleeps.append(s),
        event_time=lambda name: 100.0 if name == "drop" else None,
    )
    now[0] = 130.0                      # 30s already elapsed since the drop
    assert STEP_REGISTRY["wait_cooldown"](ctx, since="drop", s=45.0) is True
    assert sleeps == [15.0]             # only the remaining 15s


def test_wait_cooldown_without_anchor_waits_full():
    sleeps = []
    ctx = StepContext(sender=FakeSender(), clock=lambda: 0.0,
                      sleeper=lambda s: sleeps.append(s),
                      event_time=lambda name: None)
    assert STEP_REGISTRY["wait_cooldown"](ctx, since="drop", s=45.0) is True
    assert sleeps == [45.0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/flow/test_steps_events.py -v`
Expected: FAIL with `KeyError: 'wait_for_event'`

- [ ] **Step 3: Add the steps**

Append to `steps.py`:
```python
def step_wait_for_event(ctx: StepContext, *, event: str, timeout_s: float) -> bool:
    if ctx.event_waiter is None:
        return True  # no journal wiring (unit tests) -> proceed
    return ctx.event_waiter(event, timeout_s)


def step_wait_cooldown(ctx: StepContext, *, since: str, s: float) -> bool:
    anchor = ctx.event_time(since)
    if anchor is None:
        ctx.sleeper(s)            # no anchor known -> wait the full cooldown
        return True
    remaining = (anchor + s) - ctx.clock()
    if remaining > 0:
        ctx.sleeper(remaining)
    return True
```
Extend the registry:
```python
STEP_REGISTRY.update({
    "wait_for_event": step_wait_for_event,
    "wait_cooldown": step_wait_cooldown,
})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/flow/test_steps_events.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ed_autojump/flow/steps.py tests/flow/test_steps_events.py
git commit -m "feat(flow): wait_for_event + drop-anchored wait_cooldown"
```

---

## Task 4d: Vision/macro steps (sc_assist_orbit, orient_compass, pitch_compass)

**Files:**
- Modify: `src/ed_autojump/flow/steps.py`
- Test: `tests/flow/test_steps_vision.py`

`sc_assist_orbit` wraps the existing `navpanel.engage_supercruise_assist`.
`orient_compass` wraps `align_to_target`. `pitch_compass` is the compass-gated
pitch (repurposes the `pitch_star_under` logic via `_measure`).

- [ ] **Step 1: Write the failing test**

`tests/flow/test_steps_vision.py`:
```python
from ed_autojump.flow.context import StepContext
from ed_autojump.flow.steps import STEP_REGISTRY
from tests.flow import FakeSender
from ed_autojump.vision.compass import CompassRead


class FakeReader:
    """Returns a queued sequence of CompassReads, one per .read() call."""
    def __init__(self, reads):
        self._reads = list(reads)
    def read(self, frame):
        return self._reads.pop(0) if self._reads else CompassRead.not_found()


def _ctx(reader):
    sender = FakeSender()
    return StepContext(
        sender=sender,
        sleeper=lambda s: None,
        compass_reader=reader,
        frame_grabber=lambda: object(),   # any non-None frame
        compass_samples=1,                 # 1 read per measurement in tests
    ), sender


def _ahead(y):  # filled dot at vertical offset y
    return CompassRead(found=True, offset_x=0.0, offset_y=y, in_front=True, confidence=1.0)


def _behind():  # hollow dot near centre = directly astern
    return CompassRead(found=True, offset_x=0.0, offset_y=0.0, in_front=False, confidence=1.0)


def test_pitch_compass_edge_stops_when_dot_reaches_rim():
    # centred -> pitch -> near rim (magnitude >= edge_frac)
    reader = FakeReader([_ahead(0.0), _ahead(-0.7)])
    ctx, sender = _ctx(reader)
    ok = STEP_REGISTRY["pitch_compass"](ctx, until="edge", edge_frac=0.6,
                                        pitch_hold=1.0, settle_s=0.0,
                                        max_iters=5, timeout_s=999)
    assert ok is True
    assert sender.actions() == ["PitchUpButton"]   # one pitch got it to the rim


def test_pitch_compass_behind_stops_on_hollow_centre():
    reader = FakeReader([_ahead(-0.7), _behind()])
    ctx, sender = _ctx(reader)
    ok = STEP_REGISTRY["pitch_compass"](ctx, until="behind", center_frac=0.25,
                                        pitch_hold=1.0, settle_s=0.0,
                                        max_iters=5, timeout_s=999)
    assert ok is True


def test_pitch_compass_fails_closed_without_vision():
    ctx = StepContext(sender=FakeSender())   # no reader/grabber
    assert STEP_REGISTRY["pitch_compass"](ctx, until="edge") is False


def test_orient_compass_fails_closed_without_vision():
    ctx = StepContext(sender=FakeSender())
    assert STEP_REGISTRY["orient_compass"](ctx) is False


def test_orient_compass_returns_alignment_result():
    reader = FakeReader([_ahead(0.0)])             # already centred -> aligned
    ctx, _ = _ctx(reader)
    # tight tol so a centred dot counts as aligned in one measure
    assert STEP_REGISTRY["orient_compass"](ctx, align_tol=0.2, max_iters=2, timeout_s=999) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/flow/test_steps_vision.py -v`
Expected: FAIL with `KeyError: 'pitch_compass'`

- [ ] **Step 3: Add the steps**

Append to `steps.py`:
```python
def step_sc_assist_orbit(ctx: StepContext, *, settle_s: float = 0.4) -> bool:
    from ..executor.navpanel import engage_supercruise_assist
    try:
        engage_supercruise_assist(ctx.sender, sleeper=ctx.sleeper, settle_s=settle_s)
        return True
    except KeyError:
        ctx.log("BindMissing", {"step": "sc_assist_orbit"})
        return False


def step_orient_compass(ctx: StepContext, **align_overrides) -> bool:
    if ctx.compass_reader is None or ctx.frame_grabber is None:
        ctx.log("OrientNoVision", {})
        return False  # FAIL CLOSED — never proceed to jump without a confirmed orient
    from ..executor.align import align_to_target
    kwargs = dict(ctx.align_kwargs)
    kwargs.update(align_overrides)
    outcome = align_to_target(
        ctx.compass_reader,
        ctx.sender,
        capture=ctx.frame_grabber,
        clock=ctx.clock,
        sleeper=ctx.sleeper,
        samples=ctx.compass_samples,
        **kwargs,
    )
    ctx.log("Orient", {"aligned": outcome.aligned, "reason": outcome.reason,
                       "iterations": outcome.iterations})
    return bool(outcome.aligned)


def step_pitch_compass(
    ctx: StepContext,
    *,
    until: str = "edge",
    edge_frac: float = 0.6,
    center_frac: float = 0.25,
    pitch_hold: float = 1.0,
    settle_s: float = 1.0,
    max_iters: int = 20,
    timeout_s: float = 30.0,
) -> bool:
    """Compass-gated pitch. PitchUp until the TARGETED star's dot reaches the
    gate, then stop. NEVER throttles. Fails closed without vision."""
    if ctx.compass_reader is None or ctx.frame_grabber is None:
        ctx.log("PitchCompassNoVision", {"until": until})
        return False
    from ..executor.align import _measure

    def _at_gate(read) -> bool:
        if not read.found:
            return False
        if until == "behind":
            return (not read.in_front) and read.magnitude <= center_frac
        # "edge": dot near the rim (≈90° off the nose)
        return read.magnitude >= edge_frac

    start = ctx.clock()
    for i in range(max_iters):
        if ctx.clock() - start > timeout_s:
            ctx.log("PitchCompassTimeout", {"until": until, "iters": i})
            return False
        read = _measure(ctx.compass_reader, ctx.frame_grabber, ctx.compass_samples)
        if _at_gate(read):
            ctx.log("PitchCompassDone", {"until": until, "iters": i,
                                         "offset_y": read.offset_y,
                                         "in_front": read.in_front})
            return True
        ctx.sender.press("PitchUpButton", hold=pitch_hold)
        ctx.sleeper(settle_s)
    ctx.log("PitchCompassMaxIters", {"until": until})
    return False
```
Extend the registry:
```python
STEP_REGISTRY.update({
    "sc_assist_orbit": step_sc_assist_orbit,
    "orient_compass": step_orient_compass,
    "pitch_compass": step_pitch_compass,
})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/flow/test_steps_vision.py -v`
Expected: PASS (5 passed). If `orient_compass`'s centred-dot case does not
register as aligned with these knobs, adjust the test's `_ahead(0.0)` to the
align module's actual "aligned" predicate (check `executor/align.py`); the step
code is correct — only the test's expected alignment threshold may need a nudge.

- [ ] **Step 5: Commit**

```bash
git add src/ed_autojump/flow/steps.py tests/flow/test_steps_vision.py
git commit -m "feat(flow): sc_assist_orbit, orient_compass, pitch_compass steps"
```

---

## Task 5: Interpreter (sequential + retry-from + fail-closed abort)

**Files:**
- Create: `src/ed_autojump/flow/interpreter.py`
- Test: `tests/flow/test_interpreter.py`

- [ ] **Step 1: Write the failing test**

`tests/flow/test_interpreter.py`:
```python
from ed_autojump.flow.context import StepContext
from ed_autojump.flow.interpreter import run_procedure
from ed_autojump.flow.model import OnRequiredFail, Procedure, Step
from tests.flow import FakeSender


def _registry(calls, fail_actions):
    """Build a fake registry; each action appends its name and returns
    True unless it is in `fail_actions` (which return False)."""
    def make(name):
        def fn(ctx, **params):
            calls.append(name)
            return name not in fail_actions
        return fn
    return {a: make(a) for a in ("a", "b", "c", "orient", "jump")}


def test_runs_steps_in_order_to_completion():
    calls = []
    proc = Procedure(name="p", steps=(Step("a"), Step("b"), Step("c")))
    result = run_procedure(proc, StepContext(sender=FakeSender()),
                           registry=_registry(calls, set()))
    assert calls == ["a", "b", "c"]
    assert result.completed is True and result.aborted is False


def test_required_failure_aborts_without_running_later_steps():
    calls = []
    proc = Procedure(
        name="p",
        steps=(Step("a"), Step("orient", required=True), Step("jump")),
    )
    result = run_procedure(proc, StepContext(sender=FakeSender()),
                           registry=_registry(calls, {"orient"}))
    assert calls == ["a", "orient"]      # jump NEVER ran
    assert result.aborted is True and result.completed is False


def test_retry_from_resumes_then_aborts_after_max():
    calls = []
    proc = Procedure(
        name="p",
        steps=(Step("a"), Step("orient", required=True), Step("jump")),
        on_required_fail=OnRequiredFail(retry_from="a", max_retries=2, backoff_s=0.0),
    )
    result = run_procedure(proc, StepContext(sender=FakeSender()),
                           registry=_registry(calls, {"orient"}))
    # initial a,orient -> retry: a,orient -> retry: a,orient -> abort
    assert calls == ["a", "orient", "a", "orient", "a", "orient"]
    assert result.retries == 2 and result.aborted is True


def test_non_required_failure_continues():
    calls = []
    proc = Procedure(name="p", steps=(Step("a"), Step("b"), Step("c")))
    result = run_procedure(proc, StepContext(sender=FakeSender()),
                           registry=_registry(calls, {"b"}))
    assert calls == ["a", "b", "c"]
    assert result.completed is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/flow/test_interpreter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ed_autojump.flow.interpreter'`

- [ ] **Step 3: Implement the interpreter**

`src/ed_autojump/flow/interpreter.py`:
```python
"""Run a Procedure's steps in order. A failed `required` step triggers the
retry-from policy and, when exhausted, ABORTS the procedure — which never runs
later steps and therefore never throttles or jumps (fail closed)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .context import StepContext
from .model import Procedure
from .steps import STEP_REGISTRY


@dataclass
class StepResult:
    action: str
    ok: bool


@dataclass
class ProcedureResult:
    name: str
    completed: bool = False
    aborted: bool = False
    retries: int = 0
    steps: list[StepResult] = field(default_factory=list)


def run_procedure(
    proc: Procedure,
    ctx: StepContext,
    *,
    registry: Optional[dict[str, Callable[..., bool]]] = None,
) -> ProcedureResult:
    reg = registry if registry is not None else STEP_REGISTRY
    result = ProcedureResult(name=proc.name)
    i = 0
    n = len(proc.steps)
    while i < n:
        step = proc.steps[i]
        fn = reg.get(step.action)
        ok = False if fn is None else bool(fn(ctx, **step.params))
        result.steps.append(StepResult(step.action, ok))
        ctx.log("Step", {"procedure": proc.name, "action": step.action, "ok": ok})

        if not ok and step.required:
            policy = proc.on_required_fail
            target = (proc.index_of_action(policy.retry_from)
                      if policy.retry_from is not None else None)
            if target is not None and result.retries < policy.max_retries:
                result.retries += 1
                if policy.backoff_s > 0:
                    ctx.sleeper(policy.backoff_s)
                i = target
                continue
            result.aborted = True
            ctx.log("ProcedureAborted",
                    {"procedure": proc.name, "at": step.action, "retries": result.retries})
            return result
        i += 1

    result.completed = True
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/flow/test_interpreter.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ed_autojump/flow/interpreter.py tests/flow/test_interpreter.py
git commit -m "feat(flow): interpreter with retry-from + fail-closed abort"
```

---

## Task 6: Procedure data files + validation guard

**Files:**
- Create: `projects/ed-autojump/procedures/{honk,arrival,startup,smack_recovery}.toml`
- Test: `tests/flow/test_procedure_files.py`

- [ ] **Step 1: Write the failing test**

`tests/flow/test_procedure_files.py`:
```python
from pathlib import Path

from ed_autojump.flow.loader import load_procedures, validate_procedure
from ed_autojump.flow.steps import STEP_REGISTRY

PROC_DIR = Path(__file__).resolve().parents[2] / "procedures"


def test_all_procedures_load_and_validate():
    procs = load_procedures(PROC_DIR)
    assert {"honk", "arrival", "startup", "smack_recovery"} <= set(procs)
    errors = []
    for proc in procs.values():
        errors += validate_procedure(proc, known_actions=STEP_REGISTRY.keys())
    assert errors == [], errors


def test_arrival_orient_and_jump_are_required():
    procs = load_procedures(PROC_DIR)
    arrival = procs["arrival"]
    required = {s.action for s in arrival.steps if s.required}
    assert {"orient_compass", "engage_jump"} <= required
```

> `parents[2]` resolves from `tests/flow/test_procedure_files.py` up to the
> project root `projects/ed-autojump/`. Confirm the depth when you create the
> file; adjust if the test tree differs.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/flow/test_procedure_files.py -v`
Expected: FAIL (procedures dir does not exist / KeyError on missing procs)

- [ ] **Step 3: Create the four procedure files**

`projects/ed-autojump/procedures/honk.toml`:
```toml
# The one parallel track. A plain key hold; FSSDiscoveryScan confirms it landed.
parallel = true
stop_on_event = "FSSDiscoveryScan"
timeout_s = 12.0
steps = [
  { action = "press", bind = "ExplorationFSSDiscoveryScan", hold_s = 6.0 },
]
```

`projects/ed-autojump/procedures/arrival.toml`:
```toml
# Runs on every LIVE FSDJump (you arrive in supercruise). Orbit AROUND the star
# so a next-hop hidden behind it becomes reachable, THEN orient + jump.
parallel_tracks = ["honk"]

[on_required_fail]
retry_from = "sc_assist_orbit"   # re-orbit changes geometry; re-orienting alone would just fail again
max_retries = 3
backoff_s = 2.0

steps = [
  { action = "target_ahead" },
  { action = "sc_assist_orbit" },
  { action = "wait", s = 10.0 },
  { action = "target_next_route" },
  { action = "set_throttle", pct = 100 },
  { action = "wait", s = 10.0 },
  { action = "orient_compass", required = true },
  { action = "engage_jump", required = true },
]
```

`projects/ed-autojump/procedures/startup.toml`:
```toml
# Fresh load: ship sits at the star in NORMAL space. Pitch the star to the
# compass EDGE, throttle to engage the FSD, enter SC, full throttle again
# (separate axis), wait, orient, jump. No orbit (orbit is arrival-only).
parallel_tracks = ["honk"]

[on_required_fail]
retry_from = "pitch_compass"
max_retries = 3
backoff_s = 2.0

steps = [
  { action = "target_ahead" },
  { action = "pitch_compass", until = "edge", required = true },
  { action = "set_throttle", pct = 100 },
  { action = "engage_supercruise", required = true },
  { action = "set_throttle", pct = 100 },
  { action = "wait", s = 10.0 },
  { action = "target_next_route" },
  { action = "orient_compass", required = true },
  { action = "engage_jump", required = true },
]
```

`projects/ed-autojump/procedures/smack_recovery.toml`:
```toml
# Reflex: emergency drop INSIDE the star's exclusion zone (SupercruiseExit,
# BodyType:Star). Face astern during the cooldown, ignite the FSD to spawn the
# escape vector, fly it out, then clear + jump. The ~45s cooldown timer is
# anchored to the realspace drop, so the pitch below runs DURING it.
[on_required_fail]
retry_from = "pitch_compass"
max_retries = 3
backoff_s = 2.0

steps = [
  { action = "target_ahead" },
  { action = "pitch_compass", until = "behind", required = true },
  { action = "wait_cooldown", since = "drop", s = 45.0 },
  { action = "target_ahead" },
  { action = "press", bind = "Supercruise" },
  { action = "set_throttle", pct = 100 },
  { action = "orient_compass", required = true },
  { action = "wait_for_event", event = "SupercruiseEntry", timeout_s = 30.0, required = true },
  { action = "pitch_compass", until = "edge", required = true },
  { action = "wait", s = 15.0 },
  { action = "target_next_route" },
  { action = "orient_compass", required = true },
  { action = "engage_jump", required = true },
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/flow/test_procedure_files.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add projects/ed-autojump/procedures/ tests/flow/test_procedure_files.py
git commit -m "feat(flow): v1 procedure files (honk/arrival/startup/smack_recovery)"
```

---

## Task 7: FlowRunner dispatcher (offline-testable)

**Files:**
- Create: `src/ed_autojump/flow/dispatcher.py`
- Test: `tests/flow/test_dispatcher.py`

The `FlowRunner` owns event→procedure mapping, drop-time capture, the parallel
honk launch, and the live loop. We make the *dispatch* logic unit-testable by
feeding events directly (no real journal tail).

- [ ] **Step 1: Write the failing test**

`tests/flow/test_dispatcher.py`:
```python
from types import SimpleNamespace

from ed_autojump.flow.dispatcher import FlowRunner
from ed_autojump.flow.model import Procedure, Step
from tests.flow import FakeSender


def _ev(name, **fields):
    return SimpleNamespace(event=name, **fields)


def _runner(procs, sender, clock):
    return FlowRunner(
        procedures=procs,
        sender=sender,
        clock=clock,
        sleeper=lambda s: None,
        status_supplier=lambda: SimpleNamespace(
            docked=False, in_supercruise=True, fsd_charging=False,
            fsd_cooldown=False, fsd_mass_locked=False, overheating=False),
    )


def test_fsdjump_runs_arrival():
    sender = FakeSender()
    procs = {"arrival": Procedure(name="arrival", steps=(Step("target_next_route"),))}
    r = _runner(procs, sender, clock=lambda: 0.0)
    r.dispatch(_ev("FSDJump", body_type="Star"))
    assert sender.actions() == ["TargetNextRouteSystem"]


def test_supercruise_exit_at_star_runs_smack_and_records_drop_time():
    sender = FakeSender()
    procs = {"smack_recovery": Procedure(name="smack_recovery", steps=(Step("target_ahead"),))}
    t = [500.0]
    r = _runner(procs, sender, clock=lambda: t[0])
    r.dispatch(_ev("SupercruiseExit", body_type="Star"))
    assert sender.actions() == ["SelectTarget"]
    assert r.event_time("drop") == 500.0


def test_supercruise_exit_not_star_is_ignored():
    sender = FakeSender()
    procs = {"smack_recovery": Procedure(name="smack_recovery", steps=(Step("target_ahead"),))}
    r = _runner(procs, sender, clock=lambda: 0.0)
    r.dispatch(_ev("SupercruiseExit", body_type="Planet"))
    assert sender.actions() == []


def test_parallel_track_runs_alongside_main():
    sender = FakeSender()
    procs = {
        "arrival": Procedure(name="arrival", steps=(Step("target_next_route"),),
                             parallel_tracks=("honk",)),
        "honk": Procedure(name="honk", parallel=True,
                          steps=(Step("press", {"bind": "ExplorationFSSDiscoveryScan", "hold_s": 0.01}),)),
    }
    r = _runner(procs, sender, clock=lambda: 0.0)
    r.dispatch(_ev("FSDJump", body_type="Star"))
    acts = sender.actions()
    assert "ExplorationFSSDiscoveryScan" in acts   # honk fired
    assert "TargetNextRouteSystem" in acts          # arrival fired
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/flow/test_dispatcher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ed_autojump.flow.dispatcher'`

- [ ] **Step 3: Implement the dispatcher**

`src/ed_autojump/flow/dispatcher.py`:
```python
"""Map live journal events to procedures, run them through the interpreter,
launch the parallel honk track, and own the live tail/status loop.

Replaces the orchestrator's escape/engage handlers. Replay (catch-up) events
only update state; actions fire only once caught up to LIVE."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

from .context import StepContext
from .interpreter import run_procedure
from .model import Procedure


class FlowRunner:
    def __init__(
        self,
        *,
        procedures: dict[str, Procedure],
        sender: Any,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        status_supplier: Callable[[], Optional[Any]] = lambda: None,
        compass_reader: Optional[Any] = None,
        frame_grabber: Optional[Callable[[], Any]] = None,
        align_kwargs: Optional[dict] = None,
        compass_samples: int = 7,
        record: Optional[Callable[[str, Any], None]] = None,
        tail: Optional[Any] = None,
        status_reader: Optional[Any] = None,
        navroute_reader: Optional[Any] = None,
        panic_switch: Optional[Any] = None,
    ):
        self.procedures = procedures
        self.sender = sender
        self.clock = clock
        self.sleeper = sleeper
        self.status_supplier = status_supplier
        self.compass_reader = compass_reader
        self.frame_grabber = frame_grabber
        self.align_kwargs = align_kwargs or {}
        self.compass_samples = compass_samples
        self.record = record
        self.tail = tail
        self.status_reader = status_reader
        self.navroute_reader = navroute_reader
        self.panic_switch = panic_switch

        self._event_times: dict[str, float] = {}
        self._latest_status: Optional[Any] = status_supplier()
        self._caught_up = False
        self._startup_done = False
        self.stop_requested = False

    # ---- public state accessors ------------------------------------------
    def event_time(self, name: str) -> Optional[float]:
        return self._event_times.get(name)

    # ---- context construction --------------------------------------------
    def _make_context(self) -> StepContext:
        return StepContext(
            sender=self.sender,
            clock=self.clock,
            sleeper=self.sleeper,
            compass_reader=self.compass_reader,
            frame_grabber=self.frame_grabber,
            align_kwargs=self.align_kwargs,
            compass_samples=self.compass_samples,
            status_supplier=lambda: self._latest_status,
            event_time=self.event_time,
            event_waiter=self._wait_for_event,
            record=self.record,
        )

    # ---- running procedures ----------------------------------------------
    def _run(self, name: str) -> None:
        proc = self.procedures.get(name)
        if proc is None:
            return
        ctx = self._make_context()
        threads: list[threading.Thread] = []
        for track_name in proc.parallel_tracks:
            track = self.procedures.get(track_name)
            if track is None:
                continue
            th = threading.Thread(
                target=run_procedure, args=(track, self._make_context()), daemon=True
            )
            th.start()
            threads.append(th)
        run_procedure(proc, ctx)
        for th in threads:
            th.join(timeout=15.0)

    def dispatch(self, ev: Any) -> None:
        """Run the procedure mapped to a LIVE event."""
        name = getattr(ev, "event", None)
        if name == "FSDJump":
            self._run("arrival")
        elif name == "SupercruiseExit" and getattr(ev, "body_type", None) == "Star":
            self._event_times["drop"] = self.clock()
            self._run("smack_recovery")

    # ---- live loop --------------------------------------------------------
    def _wait_for_event(self, event_name: str, timeout_s: float) -> bool:
        """Poll the journal tail until `event_name` is logged or timeout."""
        if self.tail is None:
            return True  # no tail wired (unit tests) -> proceed
        deadline = self.clock() + timeout_s
        while self.clock() < deadline:
            for ev in self.tail.step():
                self._record_event_time(ev)
                self._apply_state(ev)
                if getattr(ev, "event", None) == event_name:
                    return True
            self.sleeper(0.2)
        return False

    def _record_event_time(self, ev: Any) -> None:
        name = getattr(ev, "event", None)
        if name == "SupercruiseExit" and getattr(ev, "body_type", None) == "Star":
            self._event_times["drop"] = self.clock()

    def _apply_state(self, ev: Any) -> None:
        """Hook for tracking next-target etc. State the engage gate needs is
        read live from status; route targeting is done in-procedure via
        target_next_route, so this is intentionally minimal for v1."""
        return

    def _poll_status(self) -> None:
        if self.status_reader is not None:
            st = self.status_reader.poll()
            if st is not None:
                self._latest_status = st

    def _maybe_startup(self) -> None:
        if self._startup_done:
            return
        st = self._latest_status
        if st is None:
            return
        self._startup_done = True
        if getattr(st, "docked", False):
            return  # docked on load -> nothing to escape
        self._run("startup")

    def request_stop(self) -> None:
        self.stop_requested = True

    def run_live(self, *, duration_s: float, poll_interval_s: float = 0.5) -> None:
        if self.tail is None:
            raise RuntimeError("run_live requires a journal tail")
        deadline = self.clock() + duration_s
        while not self.stop_requested and self.clock() < deadline:
            if self.panic_switch is not None and getattr(self.panic_switch, "tripped", False):
                break
            self._poll_status()
            events = self.tail.step()
            if not events:
                self._caught_up = True
                self._maybe_startup()
                self.sleeper(poll_interval_s)
                continue
            for ev in events:
                self._record_event_time(ev)
                self._apply_state(ev)
                if self._caught_up:
                    self.dispatch(ev)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/flow/test_dispatcher.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ed_autojump/flow/dispatcher.py tests/flow/test_dispatcher.py
git commit -m "feat(flow): FlowRunner dispatcher (event->procedure, parallel honk, live loop)"
```

---

## Task 8: Public exports + flow integration smoke test

**Files:**
- Modify: `src/ed_autojump/flow/__init__.py`
- Test: `tests/flow/test_integration.py`

Prove a full procedure runs end-to-end with a recording sender + fake vision —
and that a failed required orient aborts without ever pressing a throttle/jump key.

- [ ] **Step 1: Update package exports**

`src/ed_autojump/flow/__init__.py`:
```python
"""Procedure-flow engine: model, loader, steps, interpreter, dispatcher."""

from .context import StepContext
from .dispatcher import FlowRunner
from .interpreter import ProcedureResult, run_procedure
from .loader import load_procedure, load_procedures, validate_procedure
from .model import OnRequiredFail, Procedure, Step
from .steps import STEP_REGISTRY

__all__ = [
    "StepContext", "FlowRunner", "ProcedureResult", "run_procedure",
    "load_procedure", "load_procedures", "validate_procedure",
    "OnRequiredFail", "Procedure", "Step", "STEP_REGISTRY",
]
```

- [ ] **Step 2: Write the failing test**

`tests/flow/test_integration.py`:
```python
from pathlib import Path
from types import SimpleNamespace

from ed_autojump.flow import load_procedures, run_procedure, StepContext
from tests.flow import FakeSender
from ed_autojump.vision.compass import CompassRead

PROC_DIR = Path(__file__).resolve().parents[2] / "procedures"


class FakeReader:
    def __init__(self, reads):
        self._reads = list(reads)
    def read(self, frame):
        return self._reads.pop(0) if self._reads else self._reads_default
    _reads_default = CompassRead(found=True, offset_x=0.0, offset_y=0.0, in_front=True, confidence=1.0)


def _status():
    return SimpleNamespace(docked=False, in_supercruise=True, fsd_charging=False,
                           fsd_cooldown=False, fsd_mass_locked=False, overheating=False)


def test_arrival_aborts_without_jump_when_orient_fails():
    procs = load_procedures(PROC_DIR)
    sender = FakeSender()
    # reader never finds the dot -> orient_compass fails -> abort before engage_jump
    ctx = StepContext(
        sender=sender, sleeper=lambda s: None,
        compass_reader=FakeReader([CompassRead.not_found()] * 50),
        frame_grabber=lambda: object(), compass_samples=1,
        status_supplier=_status,
        align_kwargs={"max_iters": 2, "timeout_s": 999, "settle_s": 0.0},
    )
    result = run_procedure(procs["arrival"], ctx)
    assert result.aborted is True
    # The JUMP key must never fire when orient fails. (SetSpeed100 IS expected
    # earlier — it's the legitimate fly-out throttle before the orient gate —
    # so we assert on the jump combo, not the throttle.)
    assert "HyperSuperCombination" not in sender.actions()   # NEVER jumped
```

> With `FakeSender`, the `sc_assist_orbit` step's nav-panel presses are recorded
> and the step returns True, and `set_throttle pct=100` records `SetSpeed100`
> during the fly-out — both before the orient gate. The fail-closed guarantee is
> that the abort happens at `orient_compass`, so `engage_jump`'s
> `HyperSuperCombination` is never pressed. That is what this test pins.

- [ ] **Step 3: Run test to verify it fails, then passes**

Run: `python -m pytest tests/flow/test_integration.py -v`
Expected: PASS once the package `__init__` exports resolve. If it errors on the
orbit step raising instead of returning False, confirm `step_sc_assist_orbit`
catches `KeyError` (Task 4d) — it should.

- [ ] **Step 4: Commit**

```bash
git add src/ed_autojump/flow/__init__.py tests/flow/test_integration.py
git commit -m "test(flow): end-to-end fail-closed arrival integration"
```

---

## Task 9: Wire the CLI to run the FlowRunner

**Files:**
- Modify: `src/ed_autojump/cli.py` (the `cmd_run` function)
- Test: `tests/test_cli_flow.py`

Replace the `Orchestrator` construction in `cmd_run` with a `FlowRunner`. Keep
the same CLI flags (`--engage-keys`, `--journal-dir`, `--duration`, `--config`).

- [ ] **Step 1: Read the current cmd_run**

Run: `grep -n "def cmd_run" src/ed_autojump/cli.py` and read the function. Note
how it builds `sender`, `status_reader`, `navroute_reader`, `build_vision`,
`recorder`, `panic`, and how it computes `journal_dir`. You will reuse all of
that wiring and swap only the Orchestrator construction + run call.

- [ ] **Step 2: Write the failing test**

`tests/test_cli_flow.py`:
```python
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
```

> If `main`'s `run` subcommand uses different flag names, mirror them from the
> current parser (read it in Step 1). The intent: the `run` command constructs a
> FlowRunner and returns 0 on a zero-duration run.

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_cli_flow.py -v`
Expected: FAIL (cmd_run still builds Orchestrator / behavior differs)

- [ ] **Step 4: Rewrite cmd_run's construction + run**

In `src/ed_autojump/cli.py`, inside `cmd_run`, replace the `Orchestrator(...)`
construction and `orch.run_live(...)` call with:
```python
    from .flow import FlowRunner, load_procedures
    from .flow.steps import STEP_REGISTRY
    from .flow.loader import validate_procedure

    proc_dir = Path(__file__).resolve().parents[2] / "procedures"
    procedures = load_procedures(proc_dir)
    # Fail fast on an invalid procedure file rather than improvising in flight.
    problems = []
    for proc in procedures.values():
        problems += validate_procedure(proc, known_actions=STEP_REGISTRY.keys())
    if problems:
        for p in problems:
            print(f"procedure error: {p}", file=sys.stderr)
        return 2

    align_kwargs = dict(
        align_tol=cfg.vision.align_tol, deadzone=cfg.vision.deadzone,
        gain=cfg.vision.gain, min_press=cfg.vision.min_press_s,
        max_press=cfg.vision.max_press_s, search_press=cfg.vision.search_press_s,
        settle_s=cfg.vision.settle_s, max_iters=cfg.vision.max_iters,
        timeout_s=cfg.vision.timeout_s,
    )

    runner = FlowRunner(
        procedures=procedures,
        sender=sender,
        status_reader=status_reader,
        navroute_reader=navroute_reader,
        compass_reader=compass_reader,
        frame_grabber=frame_grabber,
        align_kwargs=align_kwargs,
        compass_samples=cfg.vision.align_samples,
        record=(recorder.record_outcome if recorder is not None else None),
        tail=JournalTail(journal_dir),
        panic_switch=panic,
    )
    runner.run_live(duration_s=args.duration)
    return 0
```
Ensure `import sys` and `from pathlib import Path` and `from .journal.tail import JournalTail` are present at the top of `cli.py` (add any that are missing). Delete the now-unused `Orchestrator` import + `GameState` import if nothing else in `cli.py` uses them.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_cli_flow.py -v`
Expected: PASS (1 passed)

- [ ] **Step 6: Run the full suite (catch wiring regressions)**

Run: `python -m pytest -q`
Expected: PASS (the orchestrator tests still pass — it isn't deleted yet). Fix
any import errors introduced in `cli.py`.

- [ ] **Step 7: Commit**

```bash
git add src/ed_autojump/cli.py tests/test_cli_flow.py
git commit -m "feat(cli): run the FlowRunner procedure engine instead of the orchestrator"
```

---

## Task 10: Config cleanup (resolution + drop [escape])

**Files:**
- Modify: `config.toml`, `src/ed_autojump/config.py`
- Test: `tests/test_config.py` (existing — adjust)

- [ ] **Step 1: Fix the stale resolution and remove [escape] from config.toml**

In `projects/ed-autojump/config.toml`:
- Change `target_resolution = [2560, 1440]` to `target_resolution = [1920, 1080]`
  (the game runs at 1080p; the compass region is calibrated for it).
- Delete the entire `[escape]` section (lines 83–140) — escape behaviour now
  lives in the procedure files.

- [ ] **Step 2: Update config.py**

In `src/ed_autojump/config.py`:
- Change `CvConfig.target_resolution` default to `(1920, 1080)`.
- Remove the `EscapeConfig` dataclass and the `escape` field on `Config`, and
  remove `"escape"` from the section list in `load_config`.

- [ ] **Step 3: Update + run config tests**

Edit `tests/test_config.py`: remove assertions about `cfg.escape.*`; add an
assertion that `cfg.cv.target_resolution == (1920, 1080)`.
Run: `python -m pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add config.toml src/ed_autojump/config.py tests/test_config.py
git commit -m "chore(config): 1080p resolution; drop [escape] (now in procedure files)"
```

---

## Task 11: Archive brightness; retire orchestrator + dead modules

**Files:**
- Create: `src/ed_autojump/archive/__init__.py`, `src/ed_autojump/archive/brightness/`
- Delete/retire: orchestrator escape/engage, dead executor + subsystem modules
- Test: prune/port affected tests

- [ ] **Step 1: Archive the brightness sun-avoid code**

Create `src/ed_autojump/archive/__init__.py`:
```python
"""Archived, intentionally-unwired code kept for a future version.

Nothing here is imported by the live bot. See module headers for the v2 plan."""
```
Create `src/ed_autojump/archive/brightness/__init__.py` and move the
brightness-specific functions out of `executor/escape.py` into
`src/ed_autojump/archive/brightness/sun_avoid.py`:
`sun_brightness`, `star_present`, `star_present_sampled`, `sun_avoid`,
`fly_clear`, `SunAvoidOutcome`, `FlyClearOutcome`, `perform_sensed_escape`,
`SensedEscapeOutcome`, `perform_realspace_escape`, `RealspaceEscapeOutcome`.
Add a header:
```python
"""ARCHIVED (not wired into the live bot). v2 will revive this as a GRID of
brightness checks for DIRECTIONAL star sensing. See
docs/superpowers/specs/2026-05-25-procedure-interpreter-design.md §11."""
```
Move `executor/escape.py`'s `pitch_star_under` only if nothing live needs it —
the `pitch_compass` step has its own loop, so `pitch_star_under` can be deleted.

- [ ] **Step 2: Delete the orchestrator and dead-path modules**

Delete these files (audit-confirmed zero live callers under the new engine):
```bash
git rm src/ed_autojump/orchestrator.py
git rm src/ed_autojump/executor/escape.py
git rm src/ed_autojump/executor/fss.py
git rm src/ed_autojump/executor/dss.py
git rm src/ed_autojump/executor/refuel.py
git rm src/ed_autojump/executor/scoop.py
git rm -r src/ed_autojump/docking
git rm -r src/ed_autojump/eddn
git rm -r src/ed_autojump/planner
git rm -r src/ed_autojump/hud
```
Keep: `executor/navpanel.py`, `executor/align.py`, `executor/smack_recovery.py`
(its logic now lives in the procedure, but keep the module if other tests use
it — verify with grep before deciding), `keys/`, `journal/`, `status/`,
`vision/`, `panic*`, `recorder.py`, `state.py`, `session_audit.py`.

> Do NOT delete `launcher/` in this task — it is a separate CLI surface
> (game-launch), out of the live loop but still wired to its own CLI commands.
> Leaving it avoids breaking the launch subcommands. If the user wants it gone,
> that is a separate cleanup.

- [ ] **Step 3: Delete the orphaned tests**

```bash
git rm tests/test_orchestrator*.py tests/test_escape.py tests/test_fss.py \
       tests/test_dss.py tests/test_refuel.py tests/test_scoop.py \
       tests/test_docking.py tests/test_eddn.py tests/test_planner.py \
       tests/test_hud.py tests/test_startup_escape_honk.py \
       tests/test_compass_escape.py tests/test_smack_recovery.py
```
Before deleting `tests/test_smack_recovery.py` and `tests/test_compass_escape.py`,
skim them for any still-valid assertion about `navpanel`/`align` behaviour and
port it to `tests/flow/` if it adds coverage the new tests lack.

- [ ] **Step 4: Fix remaining imports**

Run: `python -m pytest -q 2>&1 | head -40`
Resolve any `ImportError` from modules that referenced the deleted files
(likely `cli.py` leftover imports, `doctor.py`, `session_audit.py`). Remove or
repoint those imports. The `doctor` command may reference `hud/` — if so, drop
the HUD checks from `doctor.py` (out of v1 scope) or guard them.

- [ ] **Step 5: Run the full suite green**

Run: `python -m pytest -q`
Expected: PASS (all remaining tests). No references to deleted modules.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor: archive brightness; retire orchestrator + out-of-scope modules"
```

---

## Task 12: Live smoke checklist (manual, supervised)

**Files:** none (documentation of the supervised first run)

This task is a human-in-the-loop validation, not an automated test. Record
results in the commit message of any tuning change.

- [ ] **Step 1: Pre-flight**

Confirm: ED at 1080p borderless on the main monitor; ED-AFK binds preset
imported (Select Target Ahead on Key_N, etc.); SC-assist + Advanced Docking
Computer fitted; `[vision].enabled = true` with the calibrated `region`.

- [ ] **Step 2: Dry run with keys off**

Run: `ed-autojump run --duration 60`
(no `--engage-keys`) — confirm it loads procedures, tails the journal, and logs
dispatch decisions without sending keys. Check the recorder JSONL for `Step` /
`ProcedureAborted` records.

- [ ] **Step 3: Supervised live run**

Run: `ed-autojump run --engage-keys --duration 300` with a hand on the panic
hotkey. Watch one arrival cycle: target star → orbit → wait → target next →
throttle → wait → orient → jump. Confirm it does NOT charge the star. If orient
fails, confirm it ABORTS (no jump) rather than throttling.

- [ ] **Step 4: Tune the knobs in the procedure files**

Adjust `wait` durations, `pitch_compass` `edge_frac`/`center_frac`, and the
smack `wait_cooldown` based on what you observe — all in
`projects/ed-autojump/procedures/*.toml`, no code changes. Commit each tuning
change with a one-line note on what flight behaviour drove it.

- [ ] **Step 5: Verify the smack escape-vector decomposition (flagged in spec §6)**

Trigger or wait for an emergency drop at a star and confirm the
`smack_recovery` sequence clears the exclusion zone. If a single
`press Supercruise` + `orient_compass` does not auto-enter SC, edit
`smack_recovery.toml` (e.g. add a second `press Supercruise` after the
escape-vector orient). File edit, not code.

---

## Self-review notes (author)

- **Spec coverage:** step library (§4) → Tasks 4a–4d; procedure format (§5) →
  Task 2; the four procedures (§6) → Task 6; interpreter fail-closed (§3, §8) →
  Task 5 + Task 8; dispatcher/event mapping + parallel honk (§3, §3.1) → Task 7;
  config resolution + drop [escape] (§10) → Task 10; archive brightness, retire
  orchestrator + dead modules (§7) → Task 11; flight-verify items (§6, §10) →
  Task 12.
- **Type consistency:** `Step`/`Procedure`/`OnRequiredFail` (Task 1) used
  unchanged in loader (2), interpreter (5), dispatcher (7). `StepContext` field
  names (Task 3) match every step's usage (4a–4d) and the dispatcher's
  `_make_context` (7). `STEP_REGISTRY` keys match the action strings in the
  procedure files (6) and the validator (2). `run_procedure`/`ProcedureResult`
  names consistent across 5, 7, 8.
- **Known soft spots flagged inline:** the `from tests.flow import FakeSender`
  import resolving (run pytest from `projects/ed-autojump/`); `align_to_target`'s
  exact "aligned" predicate for the centred-dot test in 4d; `parents[2]` path
  depth in 6/8; exact CLI flag names in 9 — each step says to confirm against the
  real file.
```
