"""Validate the live-pulled ED-AFK binds preset.

The single source of truth for the bot's keybinds is the live preset the
operator edits IN-GAME and pulls into the repo with ``pull-binds``:

    src/ed_autojump/binds/ED-AFK.4.2.binds

This module does NOT generate or write that file — it only *checks* it.
``REQUIRED_ACTIONS`` is the contract of actions the bot's CODE presses
(derived from the codebase, not a copy of the bindings). The validator
fails closed when the live binds can't satisfy that contract:

- a REQUIRED_ACTION resolves to no bound key (the bot would KeyError mid-flight)
- two REQUIRED_ACTIONS in the SAME input context share one key (the
  "Q rolls when I page the panel" footgun)

The same-context qualifier matters: ED's context router runs flight
controls and UI/panel navigation as mutually-exclusive layers, so the
operator legitimately reuses one physical key across them (e.g. Key_D =
YawRight while flying AND UI_Right while a panel is focused). That is not a
collision; two *flight* actions on one key is.

Run as a CLI:

    python -m ed_autojump.binds_validate           # validate the shipped live binds
    python -m ed_autojump.binds_validate PATH       # validate a specific .binds
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

from .keys import parse_binds


# ── Actions the bot's CODE presses (mechanically derived) ─────────────────
# If a procedure or executor module presses an action not in this set, add
# it here. The validator requires every name to be BOUND in the live binds,
# so validation fails until the operator binds it in-game and re-pulls.
REQUIRED_ACTIONS: frozenset[str] = frozenset({
    # flow/steps.py: step_engage_jump (granular hyperspace jump, Key_K)
    "Hyperspace",
    # flow/steps.py: step_engage_supercruise, procedures/smack_recovery.toml (Key_J)
    "Supercruise",
    # flow/steps.py: step_target_ahead
    "SelectTarget",
    # flow/steps.py: step_target_next_route
    "TargetNextRouteSystem",
    # flow/steps.py: _THROTTLE_ACTION (set_throttle action)
    "SetSpeedZero", "SetSpeed25", "SetSpeed50", "SetSpeed75", "SetSpeed100",
    # flow/steps.py: step_pitch_compass, executor/align.py
    "PitchUpButton", "PitchDownButton",
    # executor/align.py
    "YawLeftButton", "YawRightButton",
    # procedures/honk.toml — the cockpit honk is the FIRE-GROUP trigger
    # (2026-06-06 probe: ExplorationFSSDiscoveryScan only works inside the
    # FSS screen; PrimaryFire in analysis mode fired the honk in 5s).
    "PrimaryFire",
    # flow/steps.py: step_ensure_analysis_mode (honk needs ANALYSIS HUD mode)
    "PlayerHUDModeToggle",
    # executor/navpanel.py (sc_assist_orbit + nav_panel_target + request_docking)
    "FocusLeftPanel", "UI_Select", "UI_Right",
    # executor/navpanel.py: request_docking — UI_Down (pin tap) + CycleNextPanel
    # (E, Navigation->Contacts tab) + UI_Up (pin hold). UI_Up/UI_Down are also
    # pressed by target_via_navpanel's pin (it predates this contract entry).
    "UI_Up", "UI_Down", "CycleNextPanel",
    # flow/dispatcher.py: heat_guard
    "DeployHeatSink",
})


# ── Input-context partition ───────────────────────────────────────────────
# ED routes keypresses through the active input context. Flight controls and
# UI/panel navigation are never live at the same instant, so the same key may
# back one action in each without conflict. A key shared by two actions in the
# SAME group below is a real collision the validator must reject.
_UI_ACTIONS: frozenset[str] = frozenset({
    "FocusLeftPanel", "UI_Select", "UI_Right",
    "UI_Up", "UI_Down", "CycleNextPanel",
})
# Everything else the bot presses is a flight control.
_FLIGHT_ACTIONS: frozenset[str] = REQUIRED_ACTIONS - _UI_ACTIONS

_CONTEXT_GROUPS: tuple[frozenset[str], ...] = (_FLIGHT_ACTIONS, _UI_ACTIONS)


class BindsValidationError(Exception):
    """Raised when the live binds can't satisfy the bot's action contract."""


def validate_live_binds(binds_path: str | Path) -> None:
    """Validate the live .binds at ``binds_path``. Raise on any failure.

    Checks:
      1. every REQUIRED_ACTION resolves to a bound key
      2. no two REQUIRED_ACTIONS in the same input context share a key
    """
    path = Path(binds_path)
    if not path.is_file():
        raise BindsValidationError(f"binds file not found: {path}")

    binds = parse_binds(path)

    # 1. Every required action must resolve to a non-empty keyboard key.
    missing = sorted(
        a for a in REQUIRED_ACTIONS
        if (b := binds.get(a)) is None or not b.key
    )
    if missing:
        raise BindsValidationError(
            f"{path.name} is missing bound key(s) for required action(s): "
            f"{missing}. The bot presses these in code; bind each in-game "
            f"and re-run `pull-binds --apply`."
        )

    # 2. Within each input context, no key may back two required actions.
    collisions: list[str] = []
    for group in _CONTEXT_GROUPS:
        seen: dict[str, str] = {}
        for action in sorted(group):
            key = binds.get(action).key
            if key in seen:
                collisions.append(
                    f"{key!r} bound to both {seen[key]} and {action}"
                )
            else:
                seen[key] = action
    if collisions:
        raise BindsValidationError(
            "duplicate key within one input context (ED does NOT suppress "
            "same-context duplicates — rebind one): " + "; ".join(collisions)
        )


# ── Driver ────────────────────────────────────────────────────────────────

_PKG_DIR = Path(__file__).parent / "binds"
_BINDS_PATH = _PKG_DIR / "ED-AFK.4.2.binds"


def _cli(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "binds", nargs="?", type=Path, default=_BINDS_PATH,
        help="path to the .binds preset to validate (default: shipped live binds)",
    )
    args = p.parse_args(list(argv) if argv is not None else None)
    try:
        validate_live_binds(args.binds)
    except BindsValidationError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    print(f"OK: {args.binds.name} -- all {len(REQUIRED_ACTIONS)} required "
          f"actions bound, no same-context key collisions")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
