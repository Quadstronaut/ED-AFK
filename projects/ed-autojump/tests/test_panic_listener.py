"""Real OS-level panic hotkey listener.

Wire-up only — we don't actually press keys in tests. We verify that the
listener is constructable, that it can register a hotkey via an injected
backend, and that the hotkey callback trips the PanicSwitch.
"""

from __future__ import annotations

import threading
import time

import pytest

from ed_autojump.panic import PanicSwitch
from ed_autojump.panic_listener import HotkeyListener, _NullBackend


class _FakeBackend:
    """Records hotkey registration; lets the test simulate a press."""

    def __init__(self):
        self.registered: list[tuple[str, callable]] = []
        self.removed_keys: list[str] = []

    def add_hotkey(self, key: str, callback) -> None:
        self.registered.append((key, callback))

    def remove_hotkey(self, key: str) -> None:
        self.removed_keys.append(key)

    def trigger(self, key: str) -> None:
        """Test helper: invoke the callback for `key`."""
        for k, cb in self.registered:
            if k == key:
                cb()


def test_listener_with_null_backend_is_a_no_op():
    panic = PanicSwitch()
    listener = HotkeyListener(panic_switch=panic, backend=_NullBackend(), hotkey="ctrl+alt+p")
    listener.start()
    listener.stop()
    assert panic.tripped is False


def test_listener_registers_configured_hotkey():
    panic = PanicSwitch()
    backend = _FakeBackend()
    listener = HotkeyListener(panic_switch=panic, backend=backend, hotkey="ctrl+alt+q")
    listener.start()
    assert backend.registered[0][0] == "ctrl+alt+q"


def test_listener_callback_trips_panic_switch():
    panic = PanicSwitch()
    backend = _FakeBackend()
    listener = HotkeyListener(panic_switch=panic, backend=backend, hotkey="ctrl+alt+p")
    listener.start()
    backend.trigger("ctrl+alt+p")
    assert panic.tripped is True


def test_listener_stop_unregisters_hotkey():
    panic = PanicSwitch()
    backend = _FakeBackend()
    listener = HotkeyListener(panic_switch=panic, backend=backend, hotkey="ctrl+alt+p")
    listener.start()
    listener.stop()
    assert "ctrl+alt+p" in backend.removed_keys


def test_listener_handles_missing_keyboard_module_gracefully():
    """If the `keyboard` lib isn't installed, fall back to NullBackend
    silently — the bot still records, just no hotkey-driven panic."""
    from ed_autojump.panic_listener import resolve_backend
    backend = resolve_backend(prefer="nonexistent_module_xyzzy")
    assert isinstance(backend, _NullBackend)
