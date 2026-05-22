"""
Journal directory tailer.

Watches a Frontier journal directory (`%USERPROFILE%\\Saved Games\\Frontier
Developments\\Elite Dangerous\\`). On startup, opens the newest
`Journal.*.log` and replays it from the top so we recover state (Loadout,
current system, etc.). On rotation (new `Journal.*.log` appears), we close
the old file and switch to the new one.

We do not use watchdog here — a simple poll loop is enough and avoids
threading complications. Callers can drive `step()` from their own loop.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Optional

from .events import Event, parse_event


JOURNAL_RE = re.compile(r"^Journal\..*\.log$")


def find_latest_journal(directory: Path) -> Optional[Path]:
    """Return the most recently modified Journal.*.log in `directory`, or None."""
    if not directory.is_dir():
        return None
    candidates = [
        p
        for p in directory.iterdir()
        if p.is_file() and JOURNAL_RE.match(p.name)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


class JournalTail:
    """
    Tails the newest journal file in `directory`. Replays from the top on
    first attach. Supports rotation: when a newer Journal.*.log appears,
    `step()` switches to it.
    """

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self._current_path: Optional[Path] = None
        self._offset: int = 0

    # ------------------------------------------------------------------
    # public API

    def step(self) -> list[Event]:
        """
        Read all new lines since the last call. Returns parsed events.

        Behaviour:
        - On first call, opens the newest journal and reads from byte 0.
        - On subsequent calls, reads from the previous offset.
        - If a newer journal has appeared, switches to it (still reads
          remaining bytes from the old one first, so we don't miss the
          tail).
        - Malformed lines are skipped silently — they are usually
          half-written final lines of a rotated file.
        """
        events: list[Event] = []

        # First, finish the old file if a rotation happened.
        latest = find_latest_journal(self.directory)
        if latest is None:
            return events

        if self._current_path is None:
            # First attach.
            self._current_path = latest
            self._offset = 0

        if latest != self._current_path:
            # Drain the old file's remaining tail, then switch.
            events.extend(self._read_new(self._current_path))
            self._current_path = latest
            self._offset = 0

        events.extend(self._read_new(self._current_path))
        return events

    def replay_file(self, path: Path | str) -> Iterator[Event]:
        """
        Replay a journal file from the start. Used for offline tests and
        for initial state recovery during attach.
        """
        with open(path, "r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    yield parse_event(raw)
                except ValueError:
                    continue

    # ------------------------------------------------------------------
    # internals

    def _read_new(self, path: Path) -> list[Event]:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return []

        if size <= self._offset:
            return []

        events: list[Event] = []
        with open(path, "r", encoding="utf-8") as fh:
            fh.seek(self._offset)
            for raw in fh:
                # If the line lacks a newline, the writer hasn't finished
                # it — back off and we'll pick it up next step().
                if not raw.endswith("\n"):
                    break
                self._offset += len(raw.encode("utf-8"))
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    events.append(parse_event(raw))
                except ValueError:
                    continue
        return events


def tail_blocking(directory: str | Path, poll_s: float = 0.5) -> Iterator[Event]:
    """Convenience: yield events from `directory` until SIGINT."""
    tail = JournalTail(directory)
    while True:
        for ev in tail.step():
            yield ev
        time.sleep(poll_s)
