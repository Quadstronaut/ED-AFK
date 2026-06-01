"""Tests for the keymap.md -> .binds generator + linter.

Lint MUST fail closed on every class of error the generator is supposed
to catch: typo'd Key names, missing required actions, duplicate keys.
The XML emitter is asserted shape-only: header, per-action block,
footer; no XML-parsing dependency in tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from ed_autojump.binds_generate import (
    LintError,
    REQUIRED_ACTIONS,
    generate,
    lint_keymap,
    parse_keymap,
    render_binds,
)


# A keymap.md with EVERY required action present and a sensible key.
_GOOD_MARKDOWN = """
# header text ignored

| Action                      | Key            | Notes |
|-----------------------------|----------------|-------|
| Hyperspace                  | Key_K          |       |
| Supercruise                 | Key_J          |       |
| SelectTarget                | Key_N          |       |
| TargetNextRouteSystem       | Key_H          |       |
| SetSpeedZero                | Key_Numpad_0   |       |
| SetSpeed25                  | Key_Numpad_2   |       |
| SetSpeed50                  | Key_Numpad_5   |       |
| SetSpeed75                  | Key_Numpad_7   |       |
| SetSpeed100                 | Key_Numpad_9   |       |
| PitchUpButton               | Key_I          |       |
| PitchDownButton             | Key_O          |       |
| YawLeftButton               | Key_A          |       |
| YawRightButton              | Key_D          |       |
| ExplorationFSSDiscoveryScan | Key_F          |       |
| FocusLeftPanel              | Key_1          |       |
| UI_Select                   | Key_Space      |       |
| UI_Right                    | Key_4          |       |
| DeployHeatSink              | Key_V          |       |
"""


# ── parse_keymap ──────────────────────────────────────────────────────────

def test_parse_extracts_all_rows():
    """Header + separator skipped; every data row captured action->key."""
    out = parse_keymap(_GOOD_MARKDOWN)
    assert set(out) == REQUIRED_ACTIONS
    assert out["Hyperspace"] == "Key_K"
    assert out["DeployHeatSink"] == "Key_V"


def test_parse_blank_key_is_empty_string():
    """A row with an empty Key cell parses as action -> '' (unbound)."""
    md = (
        "| Action | Key |\n"
        "|---|---|\n"
        "| HyperSuperCombination |  |\n"
    )
    assert parse_keymap(md)["HyperSuperCombination"] == ""


def test_parse_ignores_separator_row():
    """The |---|---| row must not become a bogus '---' action."""
    md = (
        "| Action | Key |\n"
        "|--------|-----|\n"
        "| HyperSuperCombination | Key_J |\n"
    )
    out = parse_keymap(md)
    assert "---" not in out and "--------" not in out
    assert out == {"HyperSuperCombination": "Key_J"}


# ── lint_keymap ───────────────────────────────────────────────────────────

def _good() -> dict[str, str]:
    return parse_keymap(_GOOD_MARKDOWN)


def test_lint_passes_clean_keymap():
    lint_keymap(_good())  # no raise


def test_lint_rejects_missing_required_action():
    """If the bot calls DeployHeatSink, lint must fail when keymap omits it."""
    b = _good()
    b.pop("DeployHeatSink")
    with pytest.raises(LintError, match="missing required action.*DeployHeatSink"):
        lint_keymap(b)


def test_lint_rejects_unknown_scancode():
    b = _good()
    b["HyperSuperCombination"] = "Key_NOT_REAL"
    with pytest.raises(LintError, match="unknown Key value"):
        lint_keymap(b)


def test_lint_rejects_duplicate_key():
    """Two actions sharing one key is the Q-rolls-when-paging bug class."""
    b = _good()
    b["HyperSuperCombination"] = "Key_J"
    b["DeployHeatSink"] = "Key_J"  # collision
    with pytest.raises(LintError, match="key collisions"):
        lint_keymap(b)


def test_lint_allows_multiple_blank_keys():
    """Blank Key = deliberately unbound; multiple blanks must not be 'collisions'."""
    b = _good()
    b["SetSpeed25"] = ""
    b["SetSpeed75"] = ""
    lint_keymap(b)  # no raise


# ── render_binds ──────────────────────────────────────────────────────────

def test_render_emits_header_and_footer():
    xml = render_binds(_good())
    assert xml.startswith('<?xml version="1.0"')
    assert '<Root PresetName="ED-AFK"' in xml
    assert xml.rstrip().endswith("</Root>")


def test_render_bound_key_uses_keyboard_device():
    xml = render_binds({"HyperSuperCombination": "Key_J"})
    assert '<HyperSuperCombination>' in xml
    assert '<Primary Device="Keyboard" Key="Key_J" />' in xml
    assert '<Secondary Device="{NoDevice}" Key="" />' in xml


def test_render_blank_key_emits_no_device():
    """Unbound action: Primary slot is {NoDevice} too (vs Keyboard)."""
    xml = render_binds({"HyperSuperCombination": ""})
    assert '<Primary Device="{NoDevice}" Key="" />' in xml


def test_render_preserves_insertion_order():
    """The action blocks must appear in the order they're inserted —
    keymap.md row order is the canonical order players see."""
    b = {"SelectTarget": "Key_N", "HyperSuperCombination": "Key_J"}
    xml = render_binds(b)
    assert xml.index("<SelectTarget>") < xml.index("<HyperSuperCombination>")


# ── generate (end-to-end against tmp files) ───────────────────────────────

def test_generate_writes_binds_file(tmp_path):
    keymap = tmp_path / "keymap.md"
    binds = tmp_path / "ED-AFK.4.2.binds"
    keymap.write_text(_GOOD_MARKDOWN, encoding="utf-8")
    out = generate(keymap_path=keymap, binds_path=binds)
    assert binds.exists()
    text = binds.read_text(encoding="utf-8")
    assert '<Hyperspace>' in text
    assert '<DeployHeatSink>' in text
    assert out["Hyperspace"] == "Key_K"


def test_generate_check_only_does_not_write(tmp_path):
    keymap = tmp_path / "keymap.md"
    binds = tmp_path / "ED-AFK.4.2.binds"
    keymap.write_text(_GOOD_MARKDOWN, encoding="utf-8")
    generate(keymap_path=keymap, binds_path=binds, check_only=True)
    assert not binds.exists()


def test_generate_lint_failure_does_not_write(tmp_path):
    """A LintError leaves the existing .binds file untouched — no
    half-rewritten preset."""
    keymap = tmp_path / "keymap.md"
    binds = tmp_path / "ED-AFK.4.2.binds"
    pre = "PRE-EXISTING-CONTENT"
    binds.write_text(pre, encoding="utf-8")
    # Missing every required action -> LintError before any write.
    keymap.write_text("| Action | Key |\n|---|---|\n", encoding="utf-8")
    with pytest.raises(LintError):
        generate(keymap_path=keymap, binds_path=binds)
    assert binds.read_text(encoding="utf-8") == pre


# ── shipped keymap.md (committed in-repo) ─────────────────────────────────

_REPO_KEYMAP = (
    Path(__file__).parent.parent
    / "src" / "ed_autojump" / "binds" / "keymap.md"
)


def test_shipped_keymap_parses():
    """The keymap.md committed in-repo must at least parse — even with
    all-blank Key cells (the initial empty state)."""
    bindings = parse_keymap(_REPO_KEYMAP.read_text(encoding="utf-8"))
    # Every required action must be present as a row (even if blank).
    assert REQUIRED_ACTIONS <= bindings.keys()
