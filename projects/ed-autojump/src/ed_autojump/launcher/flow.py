"""
End-to-end launch flow.

Orchestrates the four phases that get from "process not running" to
"AFK loop can take over":

  1. Dryrun pre-flight (catches stale .cred → Console.ReadLine hang)
  2. Spawn MinEdLauncher.exe with the LaunchSpec
  3. Wait for ED's main menu via SUSTAINED audio (the intro cutscene is not
     skipped — it expires on its own / the operator clicks through; its ~0.1s
     audio blip is rejected, only the menu's sustained music counts)
  4a. If menu_nav enabled: navigate Continue → PG → group → Launch,
      wait for LoadGame, verify group/commander match
  4b. If menu_nav disabled: return MAIN_MENU_READY for operator handoff

Each phase failure has a distinct status so the CLI can present a
specific error message and either continue (operator handoff) or bail.

This function is a pure orchestrator — every dependency is injected so
it tests in milliseconds against fakes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional, Protocol

from ..config import LauncherConfig, MenuNavConfig
from ..journal.events import Event, LoadGame
from ..journal.waiters import LoadGameMismatch, wait_for_load_game
from .audio_wait import (
    ED_PROCESS_NAME as ED_AUDIO_GATE_HINT,
    wait_for_ed_audio,
)
from .focus import focus_ed_window
from .launcher import DryrunOutcome, LaunchSpec, MinEdLauncher
from .menu_nav import MenuNavError, MenuNavigator


class FlowStatus(Enum):
    OK = "ok"
    DRYRUN_FAILED = "dryrun_failed"
    MAIN_MENU_TIMEOUT = "main_menu_timeout"
    MAIN_MENU_READY = "main_menu_ready"   # menu_nav disabled — operator handoff
    NAV_FAILED = "nav_failed"
    LOAD_GAME_TIMEOUT = "load_game_timeout"
    LOAD_GAME_MISMATCH = "load_game_mismatch"


@dataclass
class LaunchFlowResult:
    status: FlowStatus
    process: Optional[Any] = None
    load_game_event: Optional[LoadGame] = None
    detail: str = ""


class _MelProtocol(Protocol):
    """Minimal MEL interface the flow uses."""

    def dryrun(self, spec, *, timeout_s: float): ...
    def launch(self, spec, **kw): ...


class _TailProtocol(Protocol):
    def step(self) -> list[Event]: ...


def launch_and_enter_game(
    *,
    spec: LaunchSpec,
    mel: _MelProtocol,
    tail: _TailProtocol,
    menu_navigator: Optional[MenuNavigator],
    menu_nav_cfg: MenuNavConfig,
    launcher_cfg: LauncherConfig,
    expected_group: Optional[str] = None,
    expected_commander: Optional[str] = None,
    pre_flight_dryrun: bool = True,
    audio_probe: Optional[Callable[[], Optional[float]]] = None,
    focus_fn: Optional[Callable[[], bool]] = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    print_fn: Callable[[str], None] = print,
) -> LaunchFlowResult:
    """Run all four phases. Returns a LaunchFlowResult describing the outcome."""

    # --- Phase 1: dryrun pre-flight ------------------------------------
    if pre_flight_dryrun:
        print_fn(f"[launch] dryrun pre-flight for {spec.commander} ({spec.profile_slug})...")
        dr = mel.dryrun(spec, timeout_s=launcher_cfg.dryrun_timeout_s)
        if dr.outcome != DryrunOutcome.OK:
            return LaunchFlowResult(
                status=FlowStatus.DRYRUN_FAILED,
                detail=f"dryrun {dr.outcome.value}: {dr.stderr or 'no stderr'}",
            )
        print_fn("[launch] dryrun OK")

    # --- Phase 2: spawn MEL --------------------------------------------
    print_fn(f"[launch] spawning MinEdLauncher.exe...")
    proc = mel.launch(spec)

    # --- Phase 3: wait for the menu to become interactive --------------
    # We do NOT skip the cutscene — the operator lets it expire or clicks
    # through. The intro cutscene only emits a ~0.1s audio blip then goes
    # silent; the MAIN MENU emits sustained music. So gate on audio sustained
    # for menu_audio_sustain_s, which ignores the blip and fires only once the
    # menu music is actually playing.
    print_fn(
        f"[launch] waiting for sustained menu audio "
        f"({launcher_cfg.menu_audio_sustain_s}s) up to {launcher_cfg.launch_timeout_s}s..."
    )
    ok = wait_for_ed_audio(
        timeout_s=launcher_cfg.launch_timeout_s,
        sustain_s=launcher_cfg.menu_audio_sustain_s,
        meter_probe=audio_probe,
        clock=clock, sleep=sleep,
    )

    if not ok:
        return LaunchFlowResult(
            status=FlowStatus.MAIN_MENU_TIMEOUT,
            process=proc,
            detail=f"audio gate failed for {ED_AUDIO_GATE_HINT}",
        )

    # --- Phase 4a: operator handoff if menu_nav disabled ---------------
    if menu_navigator is None or not menu_nav_cfg.enabled:
        print_fn("[launch] menu_nav disabled — handing off to operator")
        return LaunchFlowResult(
            status=FlowStatus.MAIN_MENU_READY,
            process=proc,
            detail="enable [menu_nav] in config (after calibration) to auto-nav",
        )

    # --- Phase 4b: navigate the main menu ------------------------------
    # Focus ED before sending nav keys — SendInput is foreground-only, and the
    # terminal (or wherever) likely has focus right now. Use a generous window
    # timeout, though by here ED is plainly up (we just heard its menu music).
    _focus = focus_fn or (lambda: focus_ed_window(timeout_s=launcher_cfg.launch_timeout_s))
    print_fn("[launch] focusing ED window before menu nav...")
    if not _focus():
        print_fn("[launch] WARN: could not focus EliteDangerous64.exe — keys may miss")
    try:
        print_fn(f"[launch] navigating menu for {spec.commander}...")
        menu_navigator.navigate(commander=spec.commander)
    except MenuNavError as exc:
        return LaunchFlowResult(
            status=FlowStatus.NAV_FAILED, process=proc, detail=str(exc),
        )

    # --- Phase 4c: wait for LoadGame ----------------------------------
    print_fn(f"[launch] waiting up to {menu_nav_cfg.load_game_timeout_s}s for LoadGame...")
    try:
        lr = wait_for_load_game(
            tail,
            timeout_s=menu_nav_cfg.load_game_timeout_s,
            expected_group=expected_group,
            expected_commander=expected_commander,
            clock=clock, sleep=sleep,
        )
    except LoadGameMismatch as exc:
        return LaunchFlowResult(
            status=FlowStatus.LOAD_GAME_MISMATCH, process=proc, detail=str(exc),
        )
    if not lr.found:
        return LaunchFlowResult(
            status=FlowStatus.LOAD_GAME_TIMEOUT, process=proc,
            detail=f"no LoadGame within {menu_nav_cfg.load_game_timeout_s}s",
        )

    print_fn(f"[launch] LoadGame seen — commander={lr.event.commander} group={lr.event.group}")
    return LaunchFlowResult(
        status=FlowStatus.OK, process=proc, load_game_event=lr.event,
    )
