"""Mirror the bot's live execution info to a text stream (default stdout).

A SECOND sink alongside the EDMCOverlay writer: same duck-typed contract
(.step/.event/.status/.start/.close) so it drops into the single overlay slot
via the Tee fan-out. Purpose: the launch terminal (a Twitch stream's console)
goes silent during the jump loop because all progress flowed only to the
overlay + JSONL recorder. This echoes that progress to the terminal.

FAIL-SOFT, NON-BLOCKING: writing to a plain stream is a memory/pipe write, no
socket, no game I/O. Every method swallows its own exceptions so a broken
stream (closed pipe, encoding error) can never raise into the flight loop. No
background thread, no lock — stdout writes are already GIL-atomic per line and
this is cosmetic.
"""

from __future__ import annotations

import sys
from typing import Any, Optional, TextIO


def _ts() -> str:
    """HH:MM:SS local stamp — enough to read pacing on a stream, no date noise."""
    import time
    return time.strftime("%H:%M:%S")


class ConsoleStatusWriter:
    """Mirror the overlay duck-typed contract to a plain text stream.

    Implements .step/.event/.status/.start/.close — the FULL five-method
    contract that interpreter.py + dispatcher.py call on the overlay slot.
    Note: .status() is REQUIRED (dispatcher calls it for [ABORTED] and
    route-complete idle lines); omitting it silently drops the most important
    terminal messages.
    """

    def __init__(self, stream: Optional[TextIO] = None) -> None:
        # Resolve sys.stdout at call time if None: tests inject a StringIO;
        # default binds to the real stdout the launch console inherited.
        self._stream = stream if stream is not None else sys.stdout

    # ---- duck-typed overlay contract (must match overlay.OverlayWriter) ----

    def start(self) -> None:
        self._emit("ready", "ED-AFK console status ON")

    def close(self) -> None:
        # Nothing to tear down (no socket/thread); flush best-effort.
        try:
            self._stream.flush()
        except Exception:  # noqa: BLE001
            pass

    def step(self, procedure: str, action: str, idx: int, total: int) -> None:
        # Reuses overlay.py:214's own formatting so both sinks agree.
        self._emit("step", f"{procedure} > {action} ({idx}/{total})")

    def event(self, text: str) -> None:
        # dispatcher pre-formats: "Jump 42: Sol", "[PREEMPTED] …", etc.
        self._emit("event", text)

    def status(self, text: str) -> None:
        # dispatcher calls this for [ABORTED] and route-complete idle lines.
        self._emit("status", text)

    # ---- internals ----

    def _emit(self, tag: str, text: str) -> None:
        """One line: '[HH:MM:SS] TAG    text'. Never raises; flush so a stream
        consumer (OBS log capture, piped tee) sees it immediately."""
        try:
            self._stream.write(f"[{_ts()}] {tag:<6} {text}\n")
            self._stream.flush()
        except Exception:  # noqa: BLE001
            pass


class OverlayTee:
    """Fan a single overlay-contract call out to N sinks (overlay + console).

    The flow has exactly ONE overlay slot (StepContext.overlay, FlowRunner.overlay).
    Rather than thread a second slot everywhere, we hand that slot a Tee that
    forwards to every real sink. FAIL-SOFT PER SINK: each forward is guarded,
    so a raising/slow sink can't starve the others or reach the flight loop.
    """

    def __init__(self, *sinks: Any) -> None:
        # Drop Nones so callers can pass build_overlay()'s Optional result
        # directly (None when [overlay].enabled=false).
        self._sinks = [s for s in sinks if s is not None]

    def _fan(self, method: str, *args: Any) -> None:
        for s in self._sinks:
            fn = getattr(s, method, None)
            if fn is None:
                continue
            try:
                fn(*args)
            except Exception:  # noqa: BLE001 — one bad sink never stops the rest
                pass

    def start(self) -> None:
        self._fan("start")

    def close(self) -> None:
        self._fan("close")

    def step(self, procedure: str, action: str, idx: int, total: int) -> None:
        self._fan("step", procedure, action, idx, total)

    def event(self, text: str) -> None:
        self._fan("event", text)

    def status(self, text: str) -> None:
        self._fan("status", text)
