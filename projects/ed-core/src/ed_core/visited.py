"""Append-only log of star systems the bot visits during live runs.

One line per live FSDJump arrival: ``<timestamp>  <system>``. The file lives in
the user's Documents folder, is appended to and NEVER truncated or deleted, and
every write error is swallowed -- logging is best-effort and must never disturb
the flight loop.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


def default_visited_log_path() -> Path:
    """``~/Documents/ed-afk-systems-visited.log`` for the current user."""
    return Path.home() / "Documents" / "ed-afk-systems-visited.log"


class VisitedSystemsLogger:
    """Appends visited systems to a never-deleted log. Fail-soft by design."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else default_visited_log_path()
        # Suppresses a duplicate *consecutive* arrival into the same system
        # (e.g. a replayed line). A genuine revisit after >=1 other hop still
        # logs, because _last has moved on by then.
        self._last: Optional[str] = None

    def record(self, system: Optional[str], timestamp: Optional[str] = None) -> None:
        """Append one ``<timestamp>  <system>`` line. Never raises."""
        if not system:
            return
        if system == self._last:
            return
        line = f"{timestamp}  {system}" if timestamp else system
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            self._last = system
        except OSError:
            # Disk/permission error: drop this line, leave _last unchanged so the
            # next arrival retries. The run carries on regardless.
            pass
