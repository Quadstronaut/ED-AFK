"""Generate the ED-AFK bot binds file from `keymap.md`.

`keymap.md` is the single source of truth for the bot's binds. This
module parses its markdown table, lints the result, and writes the
resulting `ED-AFK.4.2.binds` XML next to it. Linter fails closed on:

- typo'd `Key_*` names (not in the scancode whitelist)
- duplicate keys (one key bound to two actions)
- missing bot-required actions (actions the bot's code calls that
  the keymap doesn't bind — a fresh bot procedure that presses a new
  action will refuse to ship until the keymap is updated)

Run as a CLI:

    python -m ed_autojump.binds_generate                 # regenerate
    python -m ed_autojump.binds_generate --check-only    # lint only

The generated `.binds` file is sparse: it only enumerates the actions
present in the keymap. ED accepts sparse preset files; everything not
listed is left unset.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable

from .keys.scancodes import KEY_TO_SCANCODE


# ── Actions the bot's CODE calls (mechanically derived) ───────────────────
# If a procedure or executor module presses an action not in this set,
# add it here. The linter requires every name in REQUIRED_ACTIONS to
# appear in keymap.md, so lint will fail until both are in sync.
REQUIRED_ACTIONS: frozenset[str] = frozenset({
    # flow/steps.py: step_engage_jump
    "HyperSuperCombination",
    # flow/steps.py: step_engage_supercruise, procedures/smack_recovery.toml
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
    # executor/honk.py + procedures/honk.toml
    "ExplorationFSSDiscoveryScan",
    # executor/navpanel.py (sc_assist_orbit + nav_panel_target)
    "FocusLeftPanel", "UI_Select", "UI_Right",
    # flow/dispatcher.py: heat_guard
    "DeployHeatSink",
})


PRESET_NAME = "ED-AFK"
MAJOR_VERSION = 4
MINOR_VERSION = 2


# ── Keymap parsing ────────────────────────────────────────────────────────

# Pipe-delimited markdown row; tolerate any whitespace between pipes.
_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")


def parse_keymap(text: str) -> dict[str, str]:
    """Return {action: key} from a markdown keymap document.

    Skips the header row and the separator row (any row whose Action
    cell is empty after stripping or contains only dashes). An empty
    Key cell maps to `""` (deliberately unbound).
    """
    out: dict[str, str] = {}
    saw_header = False
    for raw in text.splitlines():
        m = _ROW_RE.match(raw)
        if not m:
            continue
        cells = [c.strip() for c in m.group(1).split("|")]
        if len(cells) < 2:
            continue
        action, key = cells[0], cells[1]
        # Skip the header ("Action") and the |---|---| separator row.
        if not saw_header and action.lower() == "action":
            saw_header = True
            continue
        if not saw_header:
            continue
        if set(action) <= set("-: "):
            continue  # markdown separator row
        if not action:
            continue
        out[action] = key
    return out


# ── Lint ──────────────────────────────────────────────────────────────────

class LintError(Exception):
    """Raised when the keymap can't be safely generated from."""


def lint_keymap(bindings: dict[str, str]) -> None:
    """Validate {action: key}. Raise LintError on any issue.

    Empty-string Key is allowed (deliberate unbind). Validation:

    1. every REQUIRED_ACTIONS member appears as a key in `bindings`
    2. every non-empty Key value is in KEY_TO_SCANCODE
    3. no Key value (non-empty) appears more than once
    """
    missing = REQUIRED_ACTIONS - bindings.keys()
    if missing:
        raise LintError(
            f"keymap.md is missing required action(s): "
            f"{sorted(missing)}. The bot calls these in code; add a row "
            f"for each (Key may be left blank if you really mean unbind)."
        )

    bad_keys = [
        f"{action}={key!r}"
        for action, key in bindings.items()
        if key and key not in KEY_TO_SCANCODE
    ]
    if bad_keys:
        raise LintError(
            f"keymap.md has unknown Key value(s): {bad_keys}. Valid names "
            f"are the Key_* identifiers in keys/scancodes.py."
        )

    # Key collisions: skip blank values (multiple unbinds are fine).
    seen: dict[str, str] = {}
    collisions: list[str] = []
    for action, key in bindings.items():
        if not key:
            continue
        if key in seen:
            collisions.append(f"{key!r} bound to both {seen[key]} and {action}")
        else:
            seen[key] = action
    if collisions:
        raise LintError(
            "keymap.md has key collisions (ED's context router does NOT "
            "reliably suppress duplicates — fix each one): "
            + "; ".join(collisions)
        )


# ── XML emission ──────────────────────────────────────────────────────────

_HEADER = (
    '<?xml version="1.0" encoding="UTF-8" ?>\n'
    '<!--\n'
    '  GENERATED FILE. DO NOT HAND-EDIT.\n'
    '  Source: src/ed_autojump/binds/keymap.md\n'
    '  Regenerate: python -m ed_autojump.binds_generate\n'
    '-->\n'
    f'<Root PresetName="{PRESET_NAME}" '
    f'MajorVersion="{MAJOR_VERSION}" MinorVersion="{MINOR_VERSION}">\n'
    '  <KeyboardLayout>en-US</KeyboardLayout>\n'
)
_FOOTER = "</Root>\n"


def _action_xml(action: str, key: str) -> str:
    """Emit the `<Action><Primary/><Secondary/></Action>` block.

    Empty `key` -> Primary slot is also `Device="{NoDevice}" Key=""`,
    i.e. deliberately unbound in this preset."""
    if key:
        primary = f'    <Primary Device="Keyboard" Key="{key}" />\n'
    else:
        primary = '    <Primary Device="{NoDevice}" Key="" />\n'
    secondary = '    <Secondary Device="{NoDevice}" Key="" />\n'
    return f"  <{action}>\n{primary}{secondary}  </{action}>\n"


def render_binds(bindings: dict[str, str]) -> str:
    """Return the full binds-XML string for `bindings`. Order is keymap
    insertion order (i.e. the order rows appear in keymap.md)."""
    body = "".join(_action_xml(a, k) for a, k in bindings.items())
    return _HEADER + body + _FOOTER


# ── Driver ────────────────────────────────────────────────────────────────

_PKG_DIR = Path(__file__).parent / "binds"
_KEYMAP_PATH = _PKG_DIR / "keymap.md"
_BINDS_PATH = _PKG_DIR / "ED-AFK.4.2.binds"


def generate(
    *,
    keymap_path: Path = _KEYMAP_PATH,
    binds_path: Path = _BINDS_PATH,
    check_only: bool = False,
) -> dict[str, str]:
    """Parse, lint, and (unless check_only) regenerate the binds XML.

    Returns the parsed {action: key} bindings. Raises LintError on any
    validation failure."""
    text = keymap_path.read_text(encoding="utf-8")
    bindings = parse_keymap(text)
    lint_keymap(bindings)
    if not check_only:
        binds_path.write_text(render_binds(bindings), encoding="utf-8")
    return bindings


def _cli(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--check-only", action="store_true",
        help="Lint keymap.md; do not write the .binds file.",
    )
    args = p.parse_args(list(argv) if argv is not None else None)
    try:
        bindings = generate(check_only=args.check_only)
    except LintError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    bound = sum(1 for k in bindings.values() if k)
    unbound = len(bindings) - bound
    print(
        f"OK: {bound} bound, {unbound} blank; "
        f"{'lint passed' if args.check_only else f'wrote {_BINDS_PATH.name}'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
