"""Unit tests for pull_binds diff logic and XML round-trip."""

from __future__ import annotations

from pathlib import Path

import pytest

from ed_core.pull_binds import (
    ActionBind,
    BindSlot,
    BindsDiff,
    diff_binds,
    discover_active_preset,
    find_binds_file,
    format_diff,
    parse_binds_xml,
    pull_binds,
)


# ── Fixtures ───────────────────────────────────────────────────────────────

_MINI_BINDS = """\
<?xml version="1.0" encoding="UTF-8" ?>
<Root PresetName="TestPreset" MajorVersion="4" MinorVersion="2">
  <KeyboardLayout>en-US</KeyboardLayout>
  <HyperSuperCombination>
    <Primary Device="Keyboard" Key="Key_J" />
    <Secondary Device="{NoDevice}" Key="" />
  </HyperSuperCombination>
  <Supercruise>
    <Primary Device="Keyboard" Key="Key_K" />
    <Secondary Device="{NoDevice}" Key="" />
  </Supercruise>
  <SelectTarget>
    <Primary Device="Keyboard" Key="Key_T" />
    <Secondary Device="{NoDevice}" Key="" />
  </SelectTarget>
</Root>
"""

# Same as _MINI_BINDS but:
#   Supercruise remapped Key_K -> Key_L
#   SelectTarget removed
#   DeployHeatSink added
_LIVE_BINDS = """\
<?xml version="1.0" encoding="UTF-8" ?>
<Root PresetName="TestPreset" MajorVersion="4" MinorVersion="2">
  <KeyboardLayout>en-US</KeyboardLayout>
  <HyperSuperCombination>
    <Primary Device="Keyboard" Key="Key_J" />
    <Secondary Device="{NoDevice}" Key="" />
  </HyperSuperCombination>
  <Supercruise>
    <Primary Device="Keyboard" Key="Key_L" />
    <Secondary Device="{NoDevice}" Key="" />
  </Supercruise>
  <DeployHeatSink>
    <Primary Device="Keyboard" Key="Key_Minus" />
    <Secondary Device="{NoDevice}" Key="" />
  </DeployHeatSink>
</Root>
"""


# ── parse_binds_xml ─────────────────────────────────────────────────────────

def test_parse_skips_keyboard_layout():
    binds = parse_binds_xml(_MINI_BINDS)
    assert "KeyboardLayout" not in binds


def test_parse_returns_correct_actions():
    binds = parse_binds_xml(_MINI_BINDS)
    assert set(binds.keys()) == {"HyperSuperCombination", "Supercruise", "SelectTarget"}


def test_parse_primary_slot():
    binds = parse_binds_xml(_MINI_BINDS)
    assert binds["HyperSuperCombination"].primary == BindSlot(device="Keyboard", key="Key_J")


def test_parse_secondary_slot_unbound():
    binds = parse_binds_xml(_MINI_BINDS)
    assert not binds["HyperSuperCombination"].secondary.is_bound()


def test_parse_unbound_slot_str():
    slot = BindSlot(device="{NoDevice}", key="")
    assert str(slot) == "(unbound)"


def test_parse_bound_slot_str():
    slot = BindSlot(device="Keyboard", key="Key_J")
    assert str(slot) == "Keyboard/Key_J"


# ── diff_binds ──────────────────────────────────────────────────────────────

def _make_diff() -> BindsDiff:
    repo = parse_binds_xml(_MINI_BINDS)
    live = parse_binds_xml(_LIVE_BINDS)
    return diff_binds(repo, live)


def test_diff_detects_added():
    d = _make_diff()
    added_names = {b.action for b in d.added}
    assert "DeployHeatSink" in added_names


def test_diff_detects_removed():
    d = _make_diff()
    removed_names = {b.action for b in d.removed}
    assert "SelectTarget" in removed_names


def test_diff_detects_changed():
    d = _make_diff()
    changed_names = {repo_b.action for repo_b, _ in d.changed}
    assert "Supercruise" in changed_names


def test_diff_unchanged_action_not_in_changed():
    d = _make_diff()
    changed_names = {repo_b.action for repo_b, _ in d.changed}
    assert "HyperSuperCombination" not in changed_names


def test_diff_changed_values():
    d = _make_diff()
    repo_b, live_b = next(
        (rb, lb) for rb, lb in d.changed if rb.action == "Supercruise"
    )
    assert repo_b.primary.key == "Key_K"
    assert live_b.primary.key == "Key_L"


def test_diff_empty_when_identical():
    repo = parse_binds_xml(_MINI_BINDS)
    live = parse_binds_xml(_MINI_BINDS)
    d = diff_binds(repo, live)
    assert d.is_empty()


# ── format_diff ─────────────────────────────────────────────────────────────

def test_format_diff_empty_returns_empty_string():
    d = BindsDiff(added=[], removed=[], changed=[])
    assert format_diff(d) == ""


def test_format_diff_contains_section_headers():
    d = _make_diff()
    out = format_diff(d)
    assert "ADDED" in out
    assert "REMOVED" in out
    assert "CHANGED" in out


def test_format_diff_shows_action_names():
    d = _make_diff()
    out = format_diff(d)
    assert "DeployHeatSink" in out
    assert "SelectTarget" in out
    assert "Supercruise" in out


def test_format_diff_shows_key_before_and_after():
    d = _make_diff()
    out = format_diff(d)
    assert "Key_K" in out
    assert "Key_L" in out


# ── File discovery ──────────────────────────────────────────────────────────

def test_discover_active_preset(tmp_path: Path):
    sp = tmp_path / "StartPreset.4.start"
    sp.write_text("ConsoleX360\nMyPreset\nConsoleX360\nConsoleX360\n", encoding="utf-8")
    assert discover_active_preset(tmp_path) == "MyPreset"


def test_discover_active_preset_single_line(tmp_path: Path):
    sp = tmp_path / "StartPreset.4.start"
    sp.write_text("MyPreset\n", encoding="utf-8")
    assert discover_active_preset(tmp_path) == "MyPreset"


def test_discover_active_preset_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        discover_active_preset(tmp_path)


def test_find_binds_file_v4(tmp_path: Path):
    f = tmp_path / "MyPreset.4.0.binds"
    f.write_text(_MINI_BINDS, encoding="utf-8")
    assert find_binds_file("MyPreset", tmp_path) == f


def test_find_binds_file_v42(tmp_path: Path):
    """ED writes the highest minor version it supports (.4.2 here)."""
    f = tmp_path / "MyPreset.4.2.binds"
    f.write_text(_MINI_BINDS, encoding="utf-8")
    assert find_binds_file("MyPreset", tmp_path) == f


def test_find_binds_file_picks_highest_minor(tmp_path: Path):
    """With .4.0 AND .4.2 on disk, pick .4.2 — the one ED actually loaded."""
    (tmp_path / "MyPreset.4.0.binds").write_text(_MINI_BINDS, encoding="utf-8")
    (tmp_path / "MyPreset.4.1.binds").write_text(_MINI_BINDS, encoding="utf-8")
    high = tmp_path / "MyPreset.4.2.binds"
    high.write_text(_MINI_BINDS, encoding="utf-8")
    assert find_binds_file("MyPreset", tmp_path) == high


def test_find_binds_file_minor_sorts_numerically(tmp_path: Path):
    """.4.10 > .4.2 numerically (not lexically) — guard the int sort."""
    (tmp_path / "MyPreset.4.2.binds").write_text(_MINI_BINDS, encoding="utf-8")
    ten = tmp_path / "MyPreset.4.10.binds"
    ten.write_text(_MINI_BINDS, encoding="utf-8")
    assert find_binds_file("MyPreset", tmp_path) == ten


def test_find_binds_file_fallback(tmp_path: Path):
    f = tmp_path / "MyPreset.binds"
    f.write_text(_MINI_BINDS, encoding="utf-8")
    assert find_binds_file("MyPreset", tmp_path) == f


def test_find_binds_file_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        find_binds_file("Ghost", tmp_path)


# ── pull_binds round-trip ───────────────────────────────────────────────────

def test_pull_binds_diff_only(tmp_path: Path):
    """diff_only=True: returns the diff without touching the repo file."""
    # Write live preset into a fake bindings dir.
    live_file = tmp_path / "live_binds" / "MyPreset.4.0.binds"
    live_file.parent.mkdir()
    live_file.write_text(_LIVE_BINDS, encoding="utf-8")

    start_preset = live_file.parent / "StartPreset.4.start"
    start_preset.write_text("ConsoleX360\nMyPreset\nConsoleX360\nConsoleX360\n", encoding="utf-8")

    # Write repo preset.
    repo_file = tmp_path / "repo_preset.binds"
    repo_file.write_text(_MINI_BINDS, encoding="utf-8")

    original_repo = repo_file.read_text(encoding="utf-8")
    diff, lp, rp = pull_binds(
        bindings_dir=live_file.parent,
        repo_path=repo_file,
        apply=False,
    )

    # Repo file must NOT have changed.
    assert repo_file.read_text(encoding="utf-8") == original_repo

    added_names = {b.action for b in diff.added}
    removed_names = {b.action for b in diff.removed}
    changed_names = {rb.action for rb, _ in diff.changed}

    assert "DeployHeatSink" in added_names
    assert "SelectTarget" in removed_names
    assert "Supercruise" in changed_names


def test_pull_binds_apply_overwrites_repo(tmp_path: Path):
    """--apply: repo file is overwritten with live content."""
    live_file = tmp_path / "live_binds" / "MyPreset.4.0.binds"
    live_file.parent.mkdir()
    live_file.write_text(_LIVE_BINDS, encoding="utf-8")

    start_preset = live_file.parent / "StartPreset.4.start"
    start_preset.write_text("ConsoleX360\nMyPreset\nConsoleX360\nConsoleX360\n", encoding="utf-8")

    repo_file = tmp_path / "repo_preset.binds"
    repo_file.write_text(_MINI_BINDS, encoding="utf-8")

    pull_binds(
        bindings_dir=live_file.parent,
        repo_path=repo_file,
        apply=True,
    )

    written = repo_file.read_text(encoding="utf-8")
    assert written == _LIVE_BINDS


def test_pull_binds_preset_override(tmp_path: Path):
    """--preset NAME overrides StartPreset discovery."""
    live_file = tmp_path / "CustomPreset.4.0.binds"
    live_file.write_text(_LIVE_BINDS, encoding="utf-8")

    # No StartPreset file written — preset_name kwarg must bypass discovery.
    repo_file = tmp_path / "repo.binds"
    repo_file.write_text(_MINI_BINDS, encoding="utf-8")

    diff, lp, rp = pull_binds(
        preset_name="CustomPreset",
        bindings_dir=tmp_path,
        repo_path=repo_file,
        apply=False,
    )
    assert lp == live_file
    assert not diff.is_empty()
