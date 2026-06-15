"""
Bind-coverage safety net.

Every action name passed to `sender.press()` or `sender.hold()` anywhere
in the bot must have a working keyboard binding in the bundled
ED-AFK.4.2.binds preset. If a developer adds a new sender.press("Foo")
but forgets to bind Foo, that's a KeyError during unattended overnight
flight — discovered the next morning.

Completeness of the LIVE preset is enforced by
`binds_validate.REQUIRED_ACTIONS` against the live-pulled binds (see
`tests/test_binds_validate.py`). The test here checks every REQUIRED_ACTION
appears as a bound tag in that live file.

(The old class-conditional throttle/pitch tables this file once imported
went with perform_star_escape in the 2026-06-06 legacy purge.)
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ed_core.binds_validate import REQUIRED_ACTIONS
from ed_core.keys import parse_binds


EXECUTOR_DIR = Path(__file__).parent.parent / "src" / "ed_autojump" / "executor"
LIVE_BINDS_PATH = Path(__file__).parent.parent / "src" / "ed_autojump" / "binds" / "ED-AFK.4.2.binds"
# Populated legacy fixture used by tests that need real key bindings.
FIXTURE_BINDS_PATH = Path(__file__).parent / "fixtures" / "ED-AFK.legacy.binds"


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
    return actions


# Bind-coverage tests -------------------------------------------------------


def test_required_actions_all_bound_in_live_binds():
    """Every name in binds_validate.REQUIRED_ACTIONS must resolve to a bound
    key in the live-pulled .binds file.

    The live preset is the single source of truth — edited in-game and pulled
    with `pull-binds`. If a required action is missing or unbound there, the
    bot KeyErrors mid-flight, so this catches a stale pull before it ships.
    """
    binds = parse_binds(LIVE_BINDS_PATH)
    missing = sorted(
        a for a in REQUIRED_ACTIONS
        if binds.get(a) is None or not binds.get(a).key
    )
    assert not missing, (
        f"Live ED-AFK.4.2.binds is missing bound keys for required actions: "
        f"{missing} — bind them in-game and re-run `pull-binds --apply`."
    )


def test_executor_actions_all_bound_in_live_binds():
    """Every key the executor modules actually press must be bound in the
    live preset — a missing bind is a KeyError mid-flight."""
    binds = parse_binds(LIVE_BINDS_PATH)
    missing = sorted(
        a for a in _all_executor_actions()
        if binds.get(a) is None or not binds.get(a).key
    )
    assert not missing, f"Executor presses unbound actions: {missing}"
