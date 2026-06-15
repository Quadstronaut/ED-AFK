"""
Session recorder — writes JSONL of journal events, FSM transitions,
key-actions, and executor outcomes for later replay-based regression tests.

Per the overnight-travel goal: every bot session writes one JSONL file to
%USERPROFILE%\\ed-afk-sessions\\session_<utc-stamp>.jsonl. The recorded
sessions are the canonical safety net — `tests/test_recorded_sessions.py`
discovers and asserts on them automatically.

Schema (one JSON object per line):
  {"ts": "2026-05-22T12:00:00.123Z", "kind": "journal",  "event_name": ..., "payload": {...}}
  {"ts": "...",                       "kind": "status",   "payload": {...}}
  {"ts": "...",                       "kind": "fsm",      "from": "CHARGING", "to": "JUMPING"}
  {"ts": "...",                       "kind": "action",   "action": "PitchUpButton", "hold_s": 2.0}
  {"ts": "...",                       "kind": "outcome",  "outcome_type": "ScoopOutcome", "payload": {...}}
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel

from .journal.events import Event
from .state import State


_Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_serializable(obj: Any) -> Any:
    """Convert pydantic/dataclass/enum to JSON-friendly forms.

    Returns native types (dict/list/str/int/float/bool/None) unchanged so
    this can be called eagerly on a payload before storing it in a row.
    When used as `json.dump(..., default=_to_serializable)`, dispatch only
    fires on types json doesn't already handle.
    """
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json", by_alias=True)
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if hasattr(obj, "name") and hasattr(obj, "value") and not isinstance(obj, (str, bytes)):
        return obj.name
    if isinstance(obj, (dict, list, tuple, str, int, float, bool)) or obj is None:
        return obj
    raise TypeError(f"Not JSON-serializable: {type(obj).__name__}")


def _format_ts(dt: datetime) -> str:
    """ISO-8601 UTC with millisecond precision (matches recorded-session
    schema; the game's own journal uses second precision, we go finer for
    bot-driven actions)."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def default_session_path(*, clock: _Clock = _utcnow) -> Path:
    """Resolve where to write the next session file.

    Honors $ED_AFK_SESSIONS_DIR; falls back to ~/ed-afk-sessions/.
    Creates the directory if missing.
    """
    env = os.environ.get("ED_AFK_SESSIONS_DIR")
    base = Path(env) if env else (Path.home() / "ed-afk-sessions")
    base.mkdir(parents=True, exist_ok=True)
    stamp = clock().strftime("%Y-%m-%dT%H%M%S")
    return base / f"session_{stamp}.jsonl"


class Recorder:
    """Append-only JSONL session recorder. One file per bot session."""

    def __init__(self, path: Path, *, clock: _Clock = _utcnow):
        self.path = path
        self._clock = clock
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = path.open("w", encoding="utf-8")
        self._closed = False

    def _emit(self, row: dict) -> None:
        row.setdefault("ts", _format_ts(self._clock()))
        json.dump(row, self._fp, ensure_ascii=False, default=_to_serializable)
        self._fp.write("\n")
        self._fp.flush()

    # --- public surface -----------------------------------------------------

    def record_journal(self, ev: Event) -> None:
        self._emit({
            "kind": "journal",
            "event_name": ev.event,
            "payload": ev.model_dump(mode="json", by_alias=True),
        })

    def record_status(self, status: Any) -> None:
        self._emit({"kind": "status", "payload": _to_serializable(status)})

    def record_transition(self, from_state: State, to_state: State) -> None:
        self._emit({
            "kind": "fsm",
            "from": from_state.name,
            "to": to_state.name,
        })

    def record_action(
        self,
        action: str,
        *,
        hold_s: float = 0.0,
        extra: Optional[dict] = None,
    ) -> None:
        row: dict[str, Any] = {
            "kind": "action",
            "action": action,
            "hold_s": float(hold_s),
        }
        if extra:
            row["extra"] = extra
        self._emit(row)

    def record_outcome(self, outcome_type: str, payload: Any) -> None:
        self._emit({
            "kind": "outcome",
            "outcome_type": outcome_type,
            "payload": _to_serializable(payload),
        })

    def close(self) -> None:
        if not self._closed:
            self._fp.close()
            self._closed = True

    def __enter__(self) -> "Recorder":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
