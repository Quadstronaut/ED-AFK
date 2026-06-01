"""
Backward-sync: pull the active in-game binds preset back into the repo.

Reads the live ED preset from
  %LOCALAPPDATA%\\Frontier Developments\\Elite Dangerous\\Options\\Bindings\\

Compares it against the bundled repo preset and reports:
  - ADDED   actions present in the live file but not in the repo
  - REMOVED actions present in the repo file but not in the live file
  - CHANGED actions whose Primary or Secondary slot differs between the two

With ``--apply`` the bundled repo file is overwritten with the live preset.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# ── Types ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BindSlot:
    """One keybind slot (Primary or Secondary)."""
    device: str
    key: str

    @classmethod
    def from_element(cls, el: Optional[ET.Element]) -> "BindSlot":
        if el is None:
            return cls(device="{NoDevice}", key="")
        return cls(
            device=el.get("Device", "{NoDevice}"),
            key=el.get("Key", ""),
        )

    def is_bound(self) -> bool:
        return bool(self.key) and self.device != "{NoDevice}"

    def __str__(self) -> str:
        return f"{self.device}/{self.key}" if self.is_bound() else "(unbound)"


@dataclass(frozen=True)
class ActionBind:
    action: str
    primary: BindSlot
    secondary: BindSlot

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ActionBind):
            return NotImplemented
        return (
            self.action == other.action
            and self.primary == other.primary
            and self.secondary == other.secondary
        )


@dataclass
class BindsDiff:
    added: list[ActionBind]     # in live, not in repo
    removed: list[ActionBind]   # in repo, not in live
    changed: list[tuple[ActionBind, ActionBind]]  # (repo_bind, live_bind)

    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.changed)


# ── XML parsing ────────────────────────────────────────────────────────────


def parse_binds_xml(xml_text: str) -> dict[str, ActionBind]:
    """Parse a .binds XML string into {action_name: ActionBind}.

    Skips the <KeyboardLayout> element (not an action). Every other
    direct child of <Root> is treated as an action element."""
    root = ET.fromstring(xml_text)
    out: dict[str, ActionBind] = {}
    for child in root:
        if child.tag == "KeyboardLayout":
            continue
        action = child.tag
        primary = BindSlot.from_element(child.find("Primary"))
        secondary = BindSlot.from_element(child.find("Secondary"))
        out[action] = ActionBind(action=action, primary=primary, secondary=secondary)
    return out


# ── Diff logic ─────────────────────────────────────────────────────────────


def diff_binds(
    repo: dict[str, ActionBind],
    live: dict[str, ActionBind],
) -> BindsDiff:
    """Compute added/removed/changed between repo and live binds."""
    repo_keys = set(repo)
    live_keys = set(live)

    added = [live[k] for k in sorted(live_keys - repo_keys)]
    removed = [repo[k] for k in sorted(repo_keys - live_keys)]
    changed = [
        (repo[k], live[k])
        for k in sorted(repo_keys & live_keys)
        if repo[k] != live[k]
    ]
    return BindsDiff(added=added, removed=removed, changed=changed)


# ── Pretty-print ───────────────────────────────────────────────────────────


def format_diff(diff: BindsDiff, *, live_name: str = "live", repo_name: str = "repo") -> str:
    """Return a human-readable diff report. Empty string if no differences."""
    if diff.is_empty():
        return ""

    lines: list[str] = []
    col = 38  # action column width

    def _row(tag: str, action: str, detail: str) -> str:
        return f"  {tag:<8} {action:<{col}} {detail}"

    if diff.added:
        lines.append(f"ADDED ({len(diff.added)}) — in {live_name}, not in {repo_name}:")
        for b in diff.added:
            lines.append(_row("+", b.action, f"primary={b.primary}  secondary={b.secondary}"))

    if diff.removed:
        if lines:
            lines.append("")
        lines.append(f"REMOVED ({len(diff.removed)}) — in {repo_name}, not in {live_name}:")
        for b in diff.removed:
            lines.append(_row("-", b.action, f"primary={b.primary}  secondary={b.secondary}"))

    if diff.changed:
        if lines:
            lines.append("")
        lines.append(f"CHANGED ({len(diff.changed)}) — key assignment differs:")
        hdr = f"  {'':8} {'action':<{col}} {'repo -> live'}"
        lines.append(hdr)
        lines.append("  " + "-" * (8 + col + 40))
        for repo_b, live_b in diff.changed:
            repo_desc = f"primary={repo_b.primary}"
            live_desc = f"primary={live_b.primary}"
            if repo_b.secondary != live_b.secondary:
                repo_desc += f"  secondary={repo_b.secondary}"
                live_desc += f"  secondary={live_b.secondary}"
            lines.append(_row("~", repo_b.action, f"{repo_desc}"))
            lines.append(_row("", "", f"  --> {live_desc}"))

    return "\n".join(lines)


# ── File discovery ─────────────────────────────────────────────────────────


def _bindings_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if not local_app_data:
        raise EnvironmentError(
            "LOCALAPPDATA is not set; cannot locate ED bindings directory"
        )
    return Path(local_app_data) / "Frontier Developments" / "Elite Dangerous" / "Options" / "Bindings"


def discover_active_preset(bindings_dir: Optional[Path] = None) -> str:
    """Read StartPreset.4.start and return the cockpit preset name (line 2)."""
    bd = bindings_dir or _bindings_dir()
    # ED writes line 2 (index 1) as the ship-cockpit preset.
    for filename in ("StartPreset.4.start", "StartPreset.start"):
        p = bd / filename
        if p.is_file():
            lines = p.read_text(encoding="utf-8").splitlines()
            if len(lines) >= 2:
                return lines[1].strip()
            if lines:
                return lines[0].strip()
    raise FileNotFoundError(
        f"No StartPreset file found in {bd}. Is ED installed?"
    )


def find_binds_file(preset_name: str, bindings_dir: Optional[Path] = None) -> Path:
    """Locate the .binds file for `preset_name` in the ED bindings directory.

    ED uses the format ``<preset>.4.0.binds`` for v4 presets.  Older formats
    (no version suffix, ``.binds`` only) are also tried as fallbacks."""
    bd = bindings_dir or _bindings_dir()
    candidates = [
        bd / f"{preset_name}.4.0.binds",
        bd / f"{preset_name}.binds",
    ]
    for p in candidates:
        if p.is_file():
            return p
    raise FileNotFoundError(
        f"No .binds file found for preset {preset_name!r} in {bd}.\n"
        f"Tried: {[str(c) for c in candidates]}"
    )


# ── Bundled repo preset ────────────────────────────────────────────────────


_REPO_BINDS_PATH = Path(__file__).parent / "binds" / "ED-AFK.4.2.binds"


def repo_binds_path() -> Path:
    return _REPO_BINDS_PATH


# ── High-level API ─────────────────────────────────────────────────────────


def pull_binds(
    *,
    preset_name: Optional[str] = None,
    bindings_dir: Optional[Path] = None,
    repo_path: Optional[Path] = None,
    apply: bool = False,
) -> tuple[BindsDiff, Path, Path]:
    """
    Compare the live ED preset against the repo preset.

    Returns ``(diff, live_path, repo_path)``.  If ``apply`` is True,
    overwrites the repo file with the live content.
    """
    bd = bindings_dir or _bindings_dir()

    # Resolve live preset name.
    active_name = preset_name or discover_active_preset(bd)
    live_path = find_binds_file(active_name, bd)

    rp = repo_path or repo_binds_path()

    live_text = live_path.read_text(encoding="utf-8")
    repo_text = rp.read_text(encoding="utf-8")

    live_binds = parse_binds_xml(live_text)
    repo_binds = parse_binds_xml(repo_text)

    d = diff_binds(repo_binds, live_binds)

    if apply:
        rp.write_text(live_text, encoding="utf-8")

    return d, live_path, rp
