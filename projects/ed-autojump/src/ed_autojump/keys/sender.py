"""
DirectInput key dispatcher.

Wraps `pydirectinput` (which calls Win32 SendInput with KEYEVENTF_SCANCODE).
Resolves logical actions ("HyperSuperCombination", "ExplorationFSSDiscoveryScan")
via the parsed `.binds` file. Sends scancodes, not virtual-key codes.

Two non-real senders are provided for tests:
- NullSender: no-op
- RecordingSender: appends each press/hold to a list for assertions.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from .binds import BindsFile
from .scancodes import is_extended, scancode_for


@dataclass
class KeyEvent:
    """One press/hold/release record for diagnostics + tests."""

    timestamp: float
    action: str
    scancode: int
    modifier_scancode: Optional[int]
    hold_s: float


class Sender(ABC):
    """Abstract base. Subclasses implement `press`."""

    @abstractmethod
    def press(self, action: str, *, hold: float = 0.05) -> KeyEvent:
        ...

    def hold(self, action: str, *, hold: float) -> KeyEvent:
        return self.press(action, hold=hold)

    def release_all(self) -> None:
        """Emergency release. Default: no-op; subclasses with persistent
        key state override (NullSender stays a no-op; DirectInputSender
        sends keyUp for every scancode it has ever pressed)."""
        return None


class NullSender(Sender):
    def __init__(self, binds: Optional[BindsFile] = None):
        self.binds = binds

    def press(self, action: str, *, hold: float = 0.05) -> KeyEvent:
        return KeyEvent(
            timestamp=time.time(),
            action=action,
            scancode=0,
            modifier_scancode=None,
            hold_s=hold,
        )


class RecordingSender(Sender):
    """For tests. Records each press into `events`."""

    def __init__(self, binds: BindsFile, *, sleep_in_hold: bool = False):
        self.binds = binds
        self.events: list[KeyEvent] = []
        self._sleep_in_hold = sleep_in_hold

    def press(self, action: str, *, hold: float = 0.05) -> KeyEvent:
        binding = self.binds.get(action)
        if binding is None or not binding.key:
            raise KeyError(f"no keyboard binding for action {action!r}")
        sc = scancode_for(binding.key)
        mod_sc = scancode_for(binding.modifier) if binding.modifier else None
        ev = KeyEvent(
            timestamp=time.time(),
            action=action,
            scancode=sc,
            modifier_scancode=mod_sc,
            hold_s=hold,
        )
        self.events.append(ev)
        if self._sleep_in_hold:
            time.sleep(hold)
        return ev

    def release_all(self) -> None:
        self.events.append(KeyEvent(
            timestamp=time.time(),
            action="release_all",
            scancode=0,
            modifier_scancode=None,
            hold_s=0.0,
        ))

    def actions(self) -> list[str]:
        return [e.action for e in self.events]


class DirectInputSender(Sender):
    """
    Real sender. Uses pydirectinput's keyDown/keyUp with scancode= argument.

    pydirectinput's `keyDown(..., scancode=N)` sends SendInput with
    `KEYEVENTF_SCANCODE` flag, which is what ED accepts. Vanilla
    `pydirectinput.press('j')` works too, but going by scancode bypasses
    the library's own keyname table (which differs from ED's `Key_*` names).
    """

    def __init__(self, binds: BindsFile, *, default_hold_s: float = 0.05):
        self.binds = binds
        self.default_hold_s = default_hold_s
        self._ever_pressed: set[int] = set()
        # Defer the import so tests don't need pydirectinput at all.
        import pydirectinput

        # Bigger pause between key events than the library default (0.1s)
        # would slow us down massively; we manage timing ourselves.
        pydirectinput.PAUSE = 0.0
        self._pdi = pydirectinput

    def press(self, action: str, *, hold: float = 0.05) -> KeyEvent:
        binding = self.binds.get(action)
        if binding is None or not binding.key:
            raise KeyError(f"no keyboard binding for action {action!r}")
        sc = scancode_for(binding.key)
        mod_sc = scancode_for(binding.modifier) if binding.modifier else None

        self._ever_pressed.add(sc)
        if mod_sc is not None:
            self._ever_pressed.add(mod_sc)
            self._pdi.keyDown(None, scancode=mod_sc, _pause=False)
        self._pdi.keyDown(None, scancode=sc, _pause=False)
        try:
            time.sleep(hold)
        finally:
            self._pdi.keyUp(None, scancode=sc, _pause=False)
            if mod_sc is not None:
                self._pdi.keyUp(None, scancode=mod_sc, _pause=False)
        return KeyEvent(
            timestamp=time.time(),
            action=action,
            scancode=sc,
            modifier_scancode=mod_sc,
            hold_s=hold,
        )

    def release_all(self) -> None:
        """Send keyUp for every scancode this sender has ever pressed.

        Defensive against a panic firing during a long hold (PitchUpButton
        held 2-4s, etc.). Sends keyUp unconditionally — Windows tolerates
        a keyUp for a key that wasn't down.
        """
        for sc in list(self._ever_pressed):
            try:
                self._pdi.keyUp(None, scancode=sc, _pause=False)
            except Exception:
                # Swallow — release_all is best-effort during emergency.
                pass
