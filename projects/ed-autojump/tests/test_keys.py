"""Phase 0/1: scancode + binds parser + sender abstraction."""

from __future__ import annotations

from pathlib import Path

import pytest

from ed_autojump.keys import (
    KEY_TO_SCANCODE,
    BindsFile,
    LoggingSender,
    NullSender,
    RecordingSender,
    parse_binds,
    scancode_for,
)


# --- scancodes ----------------------------------------------------------


def test_scancode_letters_match_directinput_set1():
    # Sanity-check against the published DirectInput Set 1 make codes.
    assert scancode_for("Key_A") == 0x1E
    assert scancode_for("Key_J") == 0x24
    assert scancode_for("Key_W") == 0x11
    assert scancode_for("Key_Z") == 0x2C


def test_scancode_numpad_and_punct():
    assert scancode_for("Key_Numpad_0") == 0x52
    assert scancode_for("Key_Numpad_7") == 0x47
    assert scancode_for("Key_Apostrophe") == 0x28
    assert scancode_for("Key_SemiColon") == 0x27
    assert scancode_for("Key_Tab") == 0x0F


def test_scancode_unknown_key_raises():
    with pytest.raises(KeyError):
        scancode_for("Key_DoesNotExist")


def test_scancode_extended_keys_are_resolvable():
    assert scancode_for("Key_UpArrow") == 0x48
    assert scancode_for("Key_RightAlt") == 0x38


# --- .binds parser ------------------------------------------------------


_SAMPLE_BINDS_XML = """<?xml version="1.0" encoding="UTF-8" ?>
<Root PresetName="ED-AFK" MajorVersion="4" MinorVersion="2">
  <KeyboardLayout>en-US</KeyboardLayout>
  <HyperSuperCombination>
    <Primary Device="Keyboard" Key="Key_J" />
    <Secondary Device="{NoDevice}" Key="" />
  </HyperSuperCombination>
  <ExplorationFSSDiscoveryScan>
    <Primary Device="Keyboard" Key="Key_F" />
    <Secondary Device="{NoDevice}" Key="" />
  </ExplorationFSSDiscoveryScan>
  <SetSpeedZero>
    <Primary Device="Keyboard" Key="Key_Numpad_0" />
    <Secondary Device="{NoDevice}" Key="" />
  </SetSpeedZero>
  <ActionWithModifier>
    <Primary Device="Keyboard" Key="Key_J">
      <Modifier Device="Keyboard" Key="Key_LeftShift" />
    </Primary>
    <Secondary Device="{NoDevice}" Key="" />
  </ActionWithModifier>
  <GamepadOnlyAction>
    <Primary Device="GamePad" Key="GamePad_A" />
    <Secondary Device="{NoDevice}" Key="" />
  </GamepadOnlyAction>
  <KeyboardSecondaryOnly>
    <Primary Device="GamePad" Key="GamePad_B" />
    <Secondary Device="Keyboard" Key="Key_K" />
  </KeyboardSecondaryOnly>
</Root>
"""


def test_parse_binds_simple(tmp_path: Path):
    f = tmp_path / "x.binds"
    f.write_text(_SAMPLE_BINDS_XML, encoding="utf-8")
    b = parse_binds(f)
    assert isinstance(b, BindsFile)
    assert b.preset_name == "ED-AFK"
    assert b.major_version == 4 and b.minor_version == 2
    hc = b.get("HyperSuperCombination")
    assert hc is not None and hc.key == "Key_J"
    assert hc.modifier is None


def test_parse_binds_modifier_attached(tmp_path: Path):
    f = tmp_path / "x.binds"
    f.write_text(_SAMPLE_BINDS_XML, encoding="utf-8")
    b = parse_binds(f)
    a = b.get("ActionWithModifier")
    assert a is not None
    assert a.key == "Key_J"
    assert a.modifier == "Key_LeftShift"


def test_parse_binds_skips_non_keyboard(tmp_path: Path):
    f = tmp_path / "x.binds"
    f.write_text(_SAMPLE_BINDS_XML, encoding="utf-8")
    b = parse_binds(f)
    assert b.get("GamepadOnlyAction") is None


def test_parse_binds_promotes_secondary_keyboard(tmp_path: Path):
    """If Primary is non-keyboard but Secondary is keyboard, use Secondary."""
    f = tmp_path / "x.binds"
    f.write_text(_SAMPLE_BINDS_XML, encoding="utf-8")
    b = parse_binds(f)
    sec = b.get("KeyboardSecondaryOnly")
    assert sec is not None
    assert sec.key == "Key_K"


def test_parse_binds_has_method(tmp_path: Path):
    f = tmp_path / "x.binds"
    f.write_text(_SAMPLE_BINDS_XML, encoding="utf-8")
    b = parse_binds(f)
    assert b.has("HyperSuperCombination") is True
    assert b.has("GamepadOnlyAction") is False
    assert b.has("NotEvenInFile") is False


# --- Senders ------------------------------------------------------------


def _make_binds(tmp_path: Path) -> BindsFile:
    f = tmp_path / "x.binds"
    f.write_text(_SAMPLE_BINDS_XML, encoding="utf-8")
    return parse_binds(f)


def test_null_sender_does_nothing(tmp_path: Path):
    binds = _make_binds(tmp_path)
    s = NullSender(binds)
    ev = s.press("HyperSuperCombination")
    assert ev.action == "HyperSuperCombination"


def test_recording_sender_records_events(tmp_path: Path):
    binds = _make_binds(tmp_path)
    s = RecordingSender(binds)
    s.press("HyperSuperCombination", hold=0.01)
    s.press("SetSpeedZero", hold=0.01)
    assert s.actions() == ["HyperSuperCombination", "SetSpeedZero"]
    assert s.events[0].scancode == scancode_for("Key_J")
    assert s.events[1].scancode == scancode_for("Key_Numpad_0")


def test_recording_sender_includes_modifier(tmp_path: Path):
    binds = _make_binds(tmp_path)
    s = RecordingSender(binds)
    s.press("ActionWithModifier", hold=0.01)
    assert s.events[0].modifier_scancode == scancode_for("Key_LeftShift")


def test_recording_sender_unknown_action_raises(tmp_path: Path):
    binds = _make_binds(tmp_path)
    s = RecordingSender(binds)
    with pytest.raises(KeyError):
        s.press("NoSuchAction")


class _FakeRecorder:
    def __init__(self):
        self.actions: list[tuple] = []

    def record_action(self, action, *, hold_s=0.0, extra=None):
        self.actions.append((action, hold_s, extra))


def test_logging_sender_records_every_press(tmp_path: Path):
    """LoggingSender forwards the press to the inner sender AND logs it to the
    recorder — so the session shows exactly what the bot sent."""
    binds = _make_binds(tmp_path)
    rec = _FakeRecorder()
    inner = RecordingSender(binds)
    s = LoggingSender(inner, rec)
    ev = s.press("HyperSuperCombination", hold=1.0)
    s.press("SetSpeedZero", hold=0.05)
    # Inner sender still ran (real press happened).
    assert inner.actions() == ["HyperSuperCombination", "SetSpeedZero"]
    # And every press was logged to the recorder with its hold + scancode.
    assert [a for a, _, _ in rec.actions] == ["HyperSuperCombination", "SetSpeedZero"]
    assert rec.actions[0][1] == 1.0
    assert rec.actions[0][2]["scancode"] == ev.scancode
    # binds delegate through to the wrapped sender.
    assert s.binds is inner.binds


def test_logging_sender_logs_release_all(tmp_path: Path):
    binds = _make_binds(tmp_path)
    rec = _FakeRecorder()
    s = LoggingSender(RecordingSender(binds), rec)
    s.release_all()
    assert ("release_all", 0.0, None) in rec.actions
