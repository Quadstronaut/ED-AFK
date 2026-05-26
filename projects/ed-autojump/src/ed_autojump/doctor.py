"""
Pre-flight diagnostic checks for `ed-autojump doctor`.

Each check returns a `CheckResult` with PASS / FAIL / WARN status. The
CLI calls `run_all_checks(cfg)` and prints them; `cmd_doctor` exits
non-zero if any check returned FAIL.

WARN is informational (e.g. "EDHM not installed — that's optional");
FAIL means the bot will not function until fixed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .config import Config


CRITICAL_ACTIONS = (
    "HyperSuperCombination",   # the engage-jump key — bot can't fly without it
    "SetSpeedZero",
    "SetSpeed25",
    "SetSpeed50",
    "SetSpeed75",
    "SetSpeed100",
    "PitchUpButton",
    "ExplorationFSSDiscoveryScan",
    "ExplorationFSSEnter",
    "ExplorationFSSQuit",
)


@dataclass
class CheckResult:
    name: str
    status: str  # "PASS" | "FAIL" | "WARN"
    detail: str = ""


def _pass(name: str, detail: str = "") -> CheckResult:
    return CheckResult(name, "PASS", detail)


def _fail(name: str, detail: str) -> CheckResult:
    return CheckResult(name, "FAIL", detail)


def _warn(name: str, detail: str) -> CheckResult:
    return CheckResult(name, "WARN", detail)


def check_journal_dir_readable(journal_dir: Path) -> CheckResult:
    if not journal_dir.exists():
        return _fail(
            "journal_dir",
            f"{journal_dir} does not exist — set paths.journal_dir or pass --journal-dir",
        )
    if not journal_dir.is_dir():
        return _fail("journal_dir", f"{journal_dir} is not a directory")
    if not os.access(journal_dir, os.R_OK):
        return _fail("journal_dir", f"{journal_dir} is not readable by this user")
    return _pass("journal_dir", str(journal_dir))


def check_sessions_dir_writable(sessions_dir: Path) -> CheckResult:
    """Create the dir if missing; check we can write a probe file."""
    try:
        sessions_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _fail("sessions_dir", f"cannot create {sessions_dir}: {exc}")
    probe = sessions_dir / ".ed-autojump-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return _fail("sessions_dir", f"{sessions_dir} not writable: {exc}")
    return _pass("sessions_dir", str(sessions_dir))


def check_binds_preset(binds_path: Path) -> CheckResult:
    from .keys import parse_binds
    if not binds_path.is_file():
        return _fail("binds_preset", f"missing: {binds_path}")
    try:
        binds = parse_binds(binds_path)
    except Exception as exc:  # noqa: BLE001
        return _fail("binds_preset", f"failed to parse {binds_path.name}: {exc}")
    missing = [a for a in CRITICAL_ACTIONS if binds.get(a) is None or not binds.get(a).key]
    if missing:
        return _fail(
            "binds_preset",
            f"{binds_path.name} missing critical bindings: {missing}",
        )
    return _pass("binds_preset", f"{binds_path.name} OK")



def check_status_files(journal_dir: Path) -> CheckResult:
    status = journal_dir / "Status.json"
    navroute = journal_dir / "NavRoute.json"
    missing = []
    if not status.is_file():
        missing.append("Status.json")
    if not navroute.is_file():
        missing.append("NavRoute.json")
    if missing:
        return _warn(
            "status_files",
            f"missing in {journal_dir.name}: {missing} — game probably hasn't run yet here",
        )
    return _pass("status_files", "Status.json + NavRoute.json present")


def check_pydirectinput() -> CheckResult:
    """Verify the rgx fork is installed (upstream pydirectinput crashes us).

    FAIL — not WARN — when the wrong package is installed: with the upstream
    `pydirectinput` the bot will crash on the first key press, which is much
    worse than refusing to start.
    """
    try:
        import pydirectinput
    except ImportError as exc:
        return _warn(
            "pydirectinput",
            f"not importable: {exc} — bot can record but cannot --engage-keys",
        )
    if not hasattr(pydirectinput, "scancode_keyDown"):
        return _fail(
            "pydirectinput",
            "upstream `pydirectinput` detected — bot needs `pydirectinput-rgx>=2.0`; "
            "run `pip uninstall pydirectinput && pip install pydirectinput-rgx`",
        )
    return _pass("pydirectinput", "pydirectinput-rgx OK (scancode_keyDown available)")


def check_panic_hotkey() -> CheckResult:
    """Verify the global panic-hotkey listener can be wired."""
    from .panic_listener import _NullBackend, resolve_backend
    backend = resolve_backend()
    if isinstance(backend, _NullBackend):
        return _warn(
            "panic_hotkey",
            "no global hotkey backend — install `keyboard` (extras: hotkey) for Ctrl+Alt+P",
        )
    return _pass("panic_hotkey", f"{type(backend).__name__} ready")


def run_all_checks(cfg: Config) -> list[CheckResult]:
    """Execute every check; return the list (ordered)."""
    from . import __file__ as pkg_file
    binds_path = Path(pkg_file).parent / "binds" / "ED-AFK.4.2.binds"
    journal_dir = cfg.paths.journal_dir_expanded()
    sessions_dir = Path(os.environ.get(
        "ED_AFK_SESSIONS_DIR",
        Path.home() / "ed-afk-sessions",
    ))
    return [
        check_journal_dir_readable(journal_dir),
        check_sessions_dir_writable(sessions_dir),
        check_binds_preset(binds_path),
        check_status_files(journal_dir),
        check_pydirectinput(),
        check_panic_hotkey(),
    ]


def format_results(results: Iterable[CheckResult]) -> str:
    lines = []
    for r in results:
        marker = {"PASS": "OK  ", "FAIL": "FAIL", "WARN": "WARN"}[r.status]
        lines.append(f"  [{marker}] {r.name:24} {r.detail}")
    return "\n".join(lines)


def overall_status(results: Iterable[CheckResult]) -> int:
    """0 if no FAIL, else 1."""
    return 0 if not any(r.status == "FAIL" for r in results) else 1
