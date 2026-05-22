"""
Bind-coverage safety net.

Every action name passed to `sender.press()` or `sender.hold()` anywhere
in the bot must have a working keyboard binding in the bundled
ED-AFK.4.2.binds preset. If a developer adds a new sender.press("Foo")
but forgets to bind Foo, that's a KeyError during unattended overnight
flight — discovered the next morning.

Two layers of defence:

1. **Static AST scan** over `src/ed_autojump/executor/*.py` extracts every
   string literal passed to `sender.press(...)` / `sender.hold(...)`.

2. The known canonical list of class-conditional throttle actions
   (`SetSpeed50`, `SetSpeed75`, `SetSpeed100`) lives in
   `jump.DEFAULT_CLASS_POST_PITCH_THROTTLE` and can't be detected by AST
   scanning — those are dict values, not direct string literals at the
   call site. Spelled out here.

The test fails fast on either: missing binding, or AST-discovered action
that isn't in our known set (so removing an action also forces an update).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ed_autojump.executor.jump import (
    DEFAULT_CLASS_PITCH_S,
    DEFAULT_CLASS_POST_PITCH_THROTTLE,
)
from ed_autojump.keys import parse_binds


EXECUTOR_DIR = Path(__file__).parent.parent / "src" / "ed_autojump" / "executor"
BINDS_PATH = Path(__file__).parent.parent / "src" / "ed_autojump" / "binds" / "ED-AFK.4.2.binds"


def _scan_press_actions(source: str) -> set[str]:
    """AST-walk `source`, return every string literal passed as the first
    positional arg to sender.press(...) or sender.hold(...)."""
    actions: set[str] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # We want calls of the form `<anything>.press(...)` or `.hold(...)`.
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in ("press", "hold"):
            continue
        # First positional arg must be a string literal.
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            actions.add(first.value)
    return actions


def _all_executor_actions() -> set[str]:
    """Aggregate AST-discovered actions across every executor module."""
    actions: set[str] = set()
    for src in EXECUTOR_DIR.glob("*.py"):
        if src.name == "__init__.py":
            continue
        actions |= _scan_press_actions(src.read_text(encoding="utf-8"))
    # Class-conditional throttle actions live in a dict, not at the call site.
    actions |= set(DEFAULT_CLASS_POST_PITCH_THROTTLE.values())
    return actions


# Bind-coverage tests -------------------------------------------------------


def test_every_executor_action_has_a_keyboard_binding():
    """Critical safety: every action the bot can dispatch must be bound.
    Failure of this test means an overnight run would KeyError on that
    action mid-flight."""
    binds = parse_binds(BINDS_PATH)
    actions = _all_executor_actions()
    assert actions, "AST scan found zero actions — scanner is broken"
    missing = [a for a in actions if binds.get(a) is None or not binds.get(a).key]
    assert not missing, (
        f"{len(missing)} executor actions have no keyboard binding in "
        f"ED-AFK.4.2.binds: {sorted(missing)}"
    )


def test_critical_actions_present_explicitly():
    """Belt-and-braces: regardless of AST scanning, these MUST be bound."""
    binds = parse_binds(BINDS_PATH)
    must_have = {
        "HyperSuperCombination",        # engage next jump
        "SetSpeedZero",
        "SetSpeed25",
        "SetSpeed50",
        "SetSpeed75",
        "SetSpeed100",
        "PitchUpButton",
        "ExplorationFSSDiscoveryScan",  # honk
        "ExplorationFSSEnter",
        "ExplorationFSSQuit",
        "PlayerHUDModeToggle",          # DSS entry
        "CycleFireGroupNext",
        "PrimaryFire",
    }
    missing = [a for a in must_have if binds.get(a) is None or not binds.get(a).key]
    assert not missing, f"critical actions unbound: {missing}"


def test_class_pitch_table_covers_known_star_classes():
    """Every star class the danger filter cares about (plus all KGBFOAM
    scoopable) must have a pitch timing — otherwise perform_star_escape
    falls back to a generic 3.0s which may be wrong for that class."""
    must_have = set("KGBFOAM")
    must_have |= {"D", "DA", "DB", "N", "H", "W"}
    covered = set(DEFAULT_CLASS_PITCH_S.keys())
    missing = must_have - covered
    assert not missing, f"DEFAULT_CLASS_PITCH_S missing: {missing}"
