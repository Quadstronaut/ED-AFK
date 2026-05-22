"""
OS-level panic-hotkey listener.

Wraps a platform-specific hotkey backend (defaults to the `keyboard`
library on Windows) so the orchestrator can trip its PanicSwitch when
the user hits Ctrl+Alt+P from any window — including while the bot is
holding a long PitchUp.

The Backend abstraction is intentionally thin:

    backend.add_hotkey("ctrl+alt+p", callback)
    backend.remove_hotkey("ctrl+alt+p")

Tests substitute a FakeBackend; production uses _KeyboardBackend
(wraps the `keyboard` Python package). If the package isn't installed,
we fall back to _NullBackend so the bot still runs — just without a
hotkey panic, which is logged once at start.
"""

from __future__ import annotations

from typing import Callable, Protocol

from .panic import PanicSwitch


class _Backend(Protocol):
    def add_hotkey(self, key: str, callback: Callable[[], None]) -> None: ...
    def remove_hotkey(self, key: str) -> None: ...


class _NullBackend:
    """No-op fallback when no hotkey library is available."""

    def add_hotkey(self, key: str, callback: Callable[[], None]) -> None:
        return None

    def remove_hotkey(self, key: str) -> None:
        return None


class _KeyboardBackend:
    """Wraps the `keyboard` PyPI package. Lazy-imports it."""

    def __init__(self):
        import keyboard  # noqa: F401 — raises ImportError if missing
        self._keyboard = keyboard

    def add_hotkey(self, key: str, callback: Callable[[], None]) -> None:
        self._keyboard.add_hotkey(key, callback)

    def remove_hotkey(self, key: str) -> None:
        self._keyboard.remove_hotkey(key)


def resolve_backend(prefer: str = "keyboard") -> _Backend:
    """Return the best available backend. Falls back to _NullBackend on
    ImportError."""
    if prefer == "keyboard":
        try:
            return _KeyboardBackend()
        except ImportError:
            return _NullBackend()
    return _NullBackend()


class HotkeyListener:
    """Registers a global hotkey that trips a PanicSwitch.

    Lifecycle:
        listener = HotkeyListener(panic_switch=p, backend=resolve_backend(), hotkey="ctrl+alt+p")
        listener.start()
        ...
        listener.stop()
    """

    def __init__(
        self,
        *,
        panic_switch: PanicSwitch,
        backend: _Backend,
        hotkey: str = "ctrl+alt+p",
    ):
        self.panic_switch = panic_switch
        self.backend = backend
        self.hotkey = hotkey
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self.backend.add_hotkey(self.hotkey, self._on_press)
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        try:
            self.backend.remove_hotkey(self.hotkey)
        except Exception:
            # Best-effort — some backends raise if the key isn't registered.
            pass
        self._started = False

    def _on_press(self) -> None:
        self.panic_switch.trip()
