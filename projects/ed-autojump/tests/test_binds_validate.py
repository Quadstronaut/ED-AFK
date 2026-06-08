"""Tests for the live-binds validator.

The validator does not generate anything — it only checks the live-pulled
preset against the bot's REQUIRED_ACTIONS contract. It must fail closed when:
- a required action has no bound key
- two required actions in the SAME input context share a key
…and must PASS on the real shipped live binds (where Key_D legitimately
backs YawRightButton in flight AND UI_Right in the panel context).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ed_autojump.binds_validate import (
    REQUIRED_ACTIONS,
    BindsValidationError,
    validate_live_binds,
)


_REPO_BINDS = (
    Path(__file__).parent.parent
    / "src" / "ed_autojump" / "binds" / "ED-AFK.4.2.binds"
)


# ── synthetic .binds builders ─────────────────────────────────────────────

_XML_HEAD = (
    '<?xml version="1.0" encoding="UTF-8" ?>\n'
    '<Root PresetName="ED-AFK" MajorVersion="4" MinorVersion="2">\n'
    '  <KeyboardLayout>en-US</KeyboardLayout>\n'
)
_XML_FOOT = "</Root>\n"


def _action_xml(action: str, key: str) -> str:
    if key:
        primary = f'    <Primary Device="Keyboard" Key="{key}" />\n'
    else:
        primary = '    <Primary Device="{NoDevice}" Key="" />\n'
    return (
        f"  <{action}>\n{primary}"
        '    <Secondary Device="{NoDevice}" Key="" />\n'
        f"  </{action}>\n"
    )


def _write_binds(tmp_path: Path, bindings: dict[str, str]) -> Path:
    """Write a synthetic .binds with the given {action: key} map."""
    body = "".join(_action_xml(a, k) for a, k in bindings.items())
    p = tmp_path / "synthetic.binds"
    p.write_text(_XML_HEAD + body + _XML_FOOT, encoding="utf-8")
    return p


def _all_bound() -> dict[str, str]:
    """A binding map covering every required action, no same-context dupes.

    Mirrors the real layout: Key_D backs YawRightButton (flight) and UI_Right
    (UI) — two different contexts, deliberately allowed."""
    return {
        "Hyperspace": "Key_K",
        "Supercruise": "Key_J",
        "SelectTarget": "Key_T",
        "TargetNextRouteSystem": "Key_H",
        "SetSpeedZero": "Key_X",
        "SetSpeed25": "Key_Apostrophe",
        "SetSpeed50": "Key_LeftBracket",
        "SetSpeed75": "Key_C",
        "SetSpeed100": "Key_V",
        "PitchUpButton": "Key_S",
        "PitchDownButton": "Key_W",
        "YawLeftButton": "Key_A",
        "YawRightButton": "Key_D",
        "PrimaryFire": "Key_Numpad_Subtract",   # the honk (fire-group trigger)
        "PlayerHUDModeToggle": "Key_M",   # ensure_analysis_mode (honk gate)
        "FocusLeftPanel": "Key_1",
        "UI_Select": "Key_Enter",
        "UI_Right": "Key_D",   # same key as YawRightButton — different context, OK
        "UI_Up": "Key_W",      # same key as PitchDownButton — different context, OK
        "UI_Down": "Key_S",    # same key as PitchUpButton — different context, OK
        "CycleNextPanel": "Key_E",   # request_docking tab cycle (Navigation->Contacts)
        "DeployHeatSink": "Key_Minus",
    }


# ── failure modes ─────────────────────────────────────────────────────────

def test_missing_required_action_raises(tmp_path):
    b = _all_bound()
    b.pop("DeployHeatSink")  # the bot presses this in heat_guard
    path = _write_binds(tmp_path, b)
    with pytest.raises(BindsValidationError, match="DeployHeatSink"):
        validate_live_binds(path)


def test_unbound_required_action_raises(tmp_path):
    """A present-but-blank key counts as unbound — must fail."""
    b = _all_bound()
    b["Hyperspace"] = ""  # action element exists but no key
    path = _write_binds(tmp_path, b)
    with pytest.raises(BindsValidationError, match="Hyperspace"):
        validate_live_binds(path)


def test_same_context_duplicate_key_raises(tmp_path):
    """Two FLIGHT actions on one key is the real footgun."""
    b = _all_bound()
    b["PitchUpButton"] = "Key_S"
    b["YawLeftButton"] = "Key_S"  # both flight controls -> collision
    path = _write_binds(tmp_path, b)
    with pytest.raises(BindsValidationError, match="duplicate key"):
        validate_live_binds(path)


def test_cross_context_shared_key_passes(tmp_path):
    """Key_D backing YawRightButton (flight) + UI_Right (UI) is allowed."""
    validate_live_binds(_write_binds(tmp_path, _all_bound()))  # no raise


def test_missing_file_raises(tmp_path):
    with pytest.raises(BindsValidationError, match="not found"):
        validate_live_binds(tmp_path / "nope.binds")


# ── the real shipped live binds ───────────────────────────────────────────

def test_shipped_live_binds_passes():
    """The canonical pulled preset must validate clean."""
    validate_live_binds(_REPO_BINDS)  # no raise


def test_shipped_live_binds_has_split_fsd_keys():
    """The granular FSD split: Hyperspace=Key_K, Supercruise=Key_J."""
    from ed_autojump.keys import parse_binds

    binds = parse_binds(_REPO_BINDS)
    assert binds.get("Hyperspace").key == "Key_K"
    assert binds.get("Supercruise").key == "Key_J"


def test_required_actions_includes_split_fsd():
    assert {"Hyperspace", "Supercruise"} <= REQUIRED_ACTIONS
