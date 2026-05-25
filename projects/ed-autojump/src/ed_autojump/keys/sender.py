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


class LoggingSender(Sender):
    """Wraps a real Sender and records EVERY press to the Recorder.

    Without this the session log shows journal events + outcomes but NOT what
    keys the bot actually sent — so you can't tell "the escape never pitched"
    from "it pitched but the ship didn't respond". Each press emits a
    `kind:"action"` row (action, hold_s, scancode) with the live timestamp.

    Delegates everything it doesn't override (press_raw, _ever_pressed, binds,
    …) to the wrapped sender via __getattr__.
    """

    def __init__(self, inner: Sender, recorder):
        self._inner = inner
        self._recorder = recorder
        self.binds = getattr(inner, "binds", None)

    def _log(self, action: str, hold: float, ev: Optional["KeyEvent"] = None) -> None:
        try:
            extra = None
            if ev is not None:
                extra = {"scancode": ev.scancode,
                         "modifier_scancode": ev.modifier_scancode}
            self._recorder.record_action(action, hold_s=hold, extra=extra)
        except Exception:
            # Diagnostics must never break a keypress mid-flight.
            pass

    def press(self, action: str, *, hold: float = 0.05) -> KeyEvent:
        ev = self._inner.press(action, hold=hold)
        self._log(action, hold, ev)
        return ev

    def press_raw(self, scancode: int, *, extended: bool = False, hold: float = 0.05) -> KeyEvent:
        ev = self._inner.press_raw(scancode, extended=extended, hold=hold)
        self._log(f"raw:{scancode:#04x}", hold, ev)
        return ev

    def hold(self, action: str, *, hold: float) -> KeyEvent:
        return self.press(action, hold=hold)

    def release_all(self) -> None:
        self._inner.release_all()
        self._log("release_all", 0.0)

    def __getattr__(self, name):
        # Only called on attribute miss, so the explicit methods above win.
        return getattr(self._inner, name)


class DirectInputSender(Sender):
    """
    Real sender. Uses pydirectinput-rgx's scancode_keyDown / scancode_keyUp.

    The original `pydirectinput` (1.x) has `keyDown(key)` with no scancode
    kwarg — calling `keyDown(None, scancode=N)` raises TypeError. The
    `pydirectinput-rgx` fork (2.x+) ships dedicated scancode functions
    that wrap SendInput with KEYEVENTF_SCANCODE (which is what ED accepts).

    Extended-prefix keys (arrows, R-Ctrl/Alt, numpad Enter, etc.) need the
    0xE0 byte; rgx accepts a `ScancodeSequence([0xE0, base])` for that.
    Vanilla make codes (letters, digits, F-keys, Space, Enter, Escape) go
    in as a single int.

    Also: `press_raw(scancode, *, extended=False, hold=...)` is exposed for
    callers (the menu navigator) that need to send keys NOT defined in the
    .binds preset — arrow keys, raw Enter, Escape — to navigate ED's
    main-menu UI before the player is in-cockpit.
    """

    def __init__(self, binds: Optional[BindsFile] = None, *, default_hold_s: float = 0.05):
        self.binds = binds
        self.default_hold_s = default_hold_s
        self._ever_pressed: set[int] = set()
        # Defer the import so tests don't need pydirectinput-rgx at all.
        import pydirectinput

        # Library default pause is 0.1s between calls — we manage timing
        # ourselves and 0.1s per key would crater throughput.
        pydirectinput.PAUSE = 0.0
        self._pdi = pydirectinput
        # Verify the rgx API surface up front: if someone pip-installed the
        # wrong package (the upstream `pydirectinput`), fail loudly here
        # instead of crashing on the first jump press half an hour into
        # an unattended run.
        if not hasattr(pydirectinput, "scancode_keyDown"):
            raise RuntimeError(
                "pydirectinput is missing scancode_keyDown — you have the "
                "upstream `pydirectinput` package installed, not the required "
                "fork `pydirectinput-rgx>=2.0`. Run: pip install pydirectinput-rgx"
            )

    def _scancode_arg(self, sc: int, *, extended: bool):
        """Wrap an 8-bit scancode for rgx, prepending 0xE0 if extended."""
        if extended:
            return self._pdi.ScancodeSequence([0xE0, sc])
        return sc

    def press_raw(self, scancode: int, *, extended: bool = False, hold: float = 0.05) -> KeyEvent:
        """Press a literal scancode that isn't tied to a binds action.

        Used by the launcher's main-menu navigator (arrow keys, Enter,
        Escape) before the player is in the cockpit where binds matter.
        """
        sc_arg = self._scancode_arg(scancode, extended=extended)
        self._ever_pressed.add(scancode)
        self._pdi.scancode_keyDown(sc_arg, _pause=False)
        try:
            time.sleep(hold)
        finally:
            self._pdi.scancode_keyUp(sc_arg, _pause=False)
        return KeyEvent(
            timestamp=time.time(),
            action=f"raw:{scancode:#04x}",
            scancode=scancode,
            modifier_scancode=None,
            hold_s=hold,
        )

    def press(self, action: str, *, hold: float = 0.05) -> KeyEvent:
        if self.binds is None:
            raise RuntimeError(
                f"DirectInputSender has no binds — cannot resolve action {action!r}"
            )
        binding = self.binds.get(action)
        if binding is None or not binding.key:
            raise KeyError(f"no keyboard binding for action {action!r}")
        from .scancodes import is_extended
        sc = scancode_for(binding.key)
        sc_ext = is_extended(binding.key)
        mod_sc = scancode_for(binding.modifier) if binding.modifier else None
        mod_ext = is_extended(binding.modifier) if binding.modifier else False

        sc_arg = self._scancode_arg(sc, extended=sc_ext)
        mod_arg = self._scancode_arg(mod_sc, extended=mod_ext) if mod_sc is not None else None

        self._ever_pressed.add(sc)
        if mod_sc is not None:
            self._ever_pressed.add(mod_sc)
            self._pdi.scancode_keyDown(mod_arg, _pause=False)
        self._pdi.scancode_keyDown(sc_arg, _pause=False)
        try:
            time.sleep(hold)
        finally:
            self._pdi.scancode_keyUp(sc_arg, _pause=False)
            if mod_sc is not None:
                self._pdi.scancode_keyUp(mod_arg, _pause=False)
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
        a keyUp for a key that wasn't down. We re-derive the extended flag
        per scancode by looking at our known extended set; for raw presses
        with unknown provenance we send the plain make code, which is
        harmless even if the key was originally pressed extended.
        """
        from .scancodes import EXTENDED_KEY_TO_SCANCODE
        extended_codes = set(EXTENDED_KEY_TO_SCANCODE.values())
        for sc in list(self._ever_pressed):
            try:
                sc_arg = self._scancode_arg(sc, extended=(sc in extended_codes))
                self._pdi.scancode_keyUp(sc_arg, _pause=False)
            except Exception:
                # Swallow — release_all is best-effort during emergency.
                pass
