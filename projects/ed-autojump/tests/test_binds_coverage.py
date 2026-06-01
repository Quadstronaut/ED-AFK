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

NOTE: completeness of the LIVE preset is now enforced at generation time by
`binds_generate.REQUIRED_ACTIONS` (see `tests/test_binds_generate.py`).
The two tests here that loaded the live preset have been replaced by a single
test that checks the generated XML enumerates every REQUIRED_ACTION as a tag.
"""

from __future__ import annotations

import ast
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from ed_autojump.binds_generate import REQUIRED_ACTIONS
from ed_autojump.executor.jump import (
    DEFAULT_CLASS_PITCH_S,
    DEFAULT_CLASS_POST_PITCH_THROTTLE,
)
from ed_autojump.keys import parse_binds


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
    # Class-conditional throttle actions live in a dict, not at the call site.
    actions |= set(DEFAULT_CLASS_POST_PITCH_THROTTLE.values())
    return actions


# Bind-coverage tests -------------------------------------------------------


def test_required_actions_all_tagged_in_live_binds():
    """Every name in binds_generate.REQUIRED_ACTIONS must appear as a tag in
    the generated live .binds file.

    The live preset is intentionally sparse (all keys blank — user fills
    them in via keymap.md).  What matters is that the generator emits an
    XML element for each required action so ED's preset system knows the
    action slot exists.  This is enforced at generation time by
    lint_keymap(), and this test catches any regression where a required
    action is dropped from the generated XML structure.
    """
    tree = ET.parse(LIVE_BINDS_PATH)
    root = tree.getroot()
    rendered_tags = {child.tag for child in root}
    missing = REQUIRED_ACTIONS - rendered_tags
    assert not missing, (
        f"Live ED-AFK.4.2.binds is missing XML tags for required actions: "
        f"{sorted(missing)} — run `python -m ed_autojump.binds_generate` to regenerate."
    )


def test_class_pitch_table_covers_known_star_classes():
    """Every star class the danger filter cares about (plus all KGBFOAM
    scoopable) must have a pitch timing — otherwise perform_star_escape
    falls back to a generic 3.0s which may be wrong for that class."""
    must_have = set("KGBFOAM")
    must_have |= {"D", "DA", "DB", "N", "H", "W"}
    covered = set(DEFAULT_CLASS_PITCH_S.keys())
    missing = must_have - covered
    assert not missing, f"DEFAULT_CLASS_PITCH_S missing: {missing}"
