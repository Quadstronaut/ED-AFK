"""
DirectInputSender — rgx fork integration.

We don't actually call SendInput here; pydirectinput's scancode_keyDown /
scancode_keyUp are monkeypatched to record what would have been
dispatched, so the tests run on any CI runner (no Windows DI requirement)
and verify the call shape that matters: right scancodes, right order,
extended keys wrapped in ScancodeSequence.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from ed_autojump.keys import BindsFile, parse_binds, scancode_for


# A binds preset with a vanilla key (J), a modifier-combo, and an extended
# key (UpArrow). Covers the three press() code paths.
_BINDS_XML = """<?xml version="1.0" encoding="UTF-8" ?>
<Root PresetName="ED-AFK-test" MajorVersion="4" MinorVersion="2">
  <KeyboardLayout>en-US</KeyboardLayout>
  <HyperSuperCombination>
    <Primary Device="Keyboard" Key="Key_J" />
    <Secondary Device="{NoDevice}" Key="" />
  </HyperSuperCombination>
  <ActionWithMod>
    <Primary Device="Keyboard" Key="Key_J">
      <Modifier Device="Keyboard" Key="Key_LeftShift" />
    </Primary>
    <Secondary Device="{NoDevice}" Key="" />
  </ActionWithMod>
  <ScrollMenuUp>
    <Primary Device="Keyboard" Key="Key_UpArrow" />
    <Secondary Device="{NoDevice}" Key="" />
  </ScrollMenuUp>
</Root>
"""


def _binds(tmp_path: Path) -> BindsFile:
    p = tmp_path / "x.binds"
    p.write_text(_BINDS_XML, encoding="utf-8")
    return parse_binds(p)


@dataclass
class _PdiCall:
    fn: str
    arg: Any   # int OR ScancodeSequence-like (list)


class _FakePdi:
    """Stand-in for the pydirectinput module — records calls."""

    PAUSE = 0.0

    def __init__(self) -> None:
        self.calls: list[_PdiCall] = []
        self.ScancodeSequence = list  # rgx wraps; for the test a list suffices

    def scancode_keyDown(self, arg, *, _pause=False, **_):
        self.calls.append(_PdiCall("down", arg))
        return True

    def scancode_keyUp(self, arg, *, _pause=False, **_):
        self.calls.append(_PdiCall("up", arg))
        return True


@pytest.fixture
def fake_pdi(monkeypatch):
    """Install a fake pydirectinput module in sys.modules."""
    fake = _FakePdi()
    mod = types.ModuleType("pydirectinput")
    mod.PAUSE = fake.PAUSE
    mod.scancode_keyDown = fake.scancode_keyDown
    mod.scancode_keyUp = fake.scancode_keyUp
    mod.ScancodeSequence = fake.ScancodeSequence
    monkeypatch.setitem(sys.modules, "pydirectinput", mod)
    return fake


def test_constructor_raises_if_upstream_pydirectinput(monkeypatch):
    """When the wrong package is installed (no scancode_keyDown attribute),
    the sender must refuse to start — not crash on the first press."""
    bad = types.ModuleType("pydirectinput")
    bad.PAUSE = 0.0
    # NB: no scancode_keyDown attribute
    monkeypatch.setitem(sys.modules, "pydirectinput", bad)
    # Defer import to inside the test so the fake is in place first.
    from ed_autojump.keys.sender import DirectInputSender
    with pytest.raises(RuntimeError, match="pydirectinput-rgx"):
        DirectInputSender(binds=None)


def test_press_dispatches_scancode_in_correct_order(fake_pdi, tmp_path):
    from ed_autojump.keys.sender import DirectInputSender
    s = DirectInputSender(binds=_binds(tmp_path))
    s.press("HyperSuperCombination", hold=0.0)
    # No modifier → just down then up of Key_J (0x24).
    assert [c.fn for c in fake_pdi.calls] == ["down", "up"]
    assert fake_pdi.calls[0].arg == scancode_for("Key_J")
    assert fake_pdi.calls[1].arg == scancode_for("Key_J")


def test_press_with_modifier_orders_mod_outside_key(fake_pdi, tmp_path):
    from ed_autojump.keys.sender import DirectInputSender
    s = DirectInputSender(binds=_binds(tmp_path))
    s.press("ActionWithMod", hold=0.0)
    # Order must be: mod down, key down, key up, mod up.
    assert [c.fn for c in fake_pdi.calls] == ["down", "down", "up", "up"]
    assert fake_pdi.calls[0].arg == scancode_for("Key_LeftShift")
    assert fake_pdi.calls[1].arg == scancode_for("Key_J")
    assert fake_pdi.calls[2].arg == scancode_for("Key_J")
    assert fake_pdi.calls[3].arg == scancode_for("Key_LeftShift")


def test_press_extended_key_wraps_in_scancode_sequence(fake_pdi, tmp_path):
    from ed_autojump.keys.sender import DirectInputSender
    s = DirectInputSender(binds=_binds(tmp_path))
    s.press("ScrollMenuUp", hold=0.0)
    # Extended key (UpArrow) → both down and up args are [0xE0, 0x48].
    assert fake_pdi.calls[0].fn == "down"
    assert fake_pdi.calls[0].arg == [0xE0, scancode_for("Key_UpArrow")]
    assert fake_pdi.calls[1].fn == "up"
    assert fake_pdi.calls[1].arg == [0xE0, scancode_for("Key_UpArrow")]


def test_press_raw_supports_arbitrary_scancode(fake_pdi):
    """For menu navigation — Enter (0x1C), Escape (0x01) — not in any
    binds, sender must still dispatch them on demand."""
    from ed_autojump.keys.sender import DirectInputSender
    s = DirectInputSender(binds=None)
    s.press_raw(0x1C, hold=0.0)  # Enter
    assert fake_pdi.calls == [_PdiCall("down", 0x1C), _PdiCall("up", 0x1C)]


def test_press_raw_extended_wraps_scancode_sequence(fake_pdi):
    """Arrow keys, page-up/down, R-Ctrl/Alt — extended scancodes."""
    from ed_autojump.keys.sender import DirectInputSender
    s = DirectInputSender(binds=None)
    s.press_raw(0x50, extended=True, hold=0.0)  # DownArrow
    assert fake_pdi.calls[0].arg == [0xE0, 0x50]
    assert fake_pdi.calls[1].arg == [0xE0, 0x50]


def test_release_all_replays_keyup_for_every_pressed(fake_pdi, tmp_path):
    """Emergency release — every key the sender has ever pressed must
    receive a keyUp, even if its press path already released it."""
    from ed_autojump.keys.sender import DirectInputSender
    s = DirectInputSender(binds=_binds(tmp_path))
    s.press("HyperSuperCombination", hold=0.0)
    s.press("ScrollMenuUp", hold=0.0)
    fake_pdi.calls.clear()
    s.release_all()
    # Two scancodes were pressed (J, UpArrow). release_all sends keyUp for both.
    ups = [c for c in fake_pdi.calls if c.fn == "up"]
    assert len(ups) == 2
    args = {tuple(c.arg) if isinstance(c.arg, list) else c.arg for c in ups}
    assert scancode_for("Key_J") in args
    assert (0xE0, scancode_for("Key_UpArrow")) in args


def test_press_without_binds_raises_for_named_action(fake_pdi):
    """press(action_name) requires binds. press_raw(scancode) does not."""
    from ed_autojump.keys.sender import DirectInputSender
    s = DirectInputSender(binds=None)
    with pytest.raises(RuntimeError, match="no binds"):
        s.press("HyperSuperCombination")
