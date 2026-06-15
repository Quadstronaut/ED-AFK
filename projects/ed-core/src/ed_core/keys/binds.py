"""
.binds XML parser.

Reads a Frontier `.binds` file and exposes an action -> keyboard-binding map.
Non-keyboard devices (gamepad, HOTAS, mouse) are ignored — we can only
synthesize keystrokes. If both Primary and Secondary are non-keyboard, the
action has no usable binding for the bot and the caller must surface an
error.

Pattern adapted from SumZer0-git/EDAPGui's EDKeys.py (MIT).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Binding:
    """One keyboard binding for one action. Modifier is optional."""

    action: str
    key: str
    modifier: Optional[str] = None
    secondary_key: Optional[str] = None
    secondary_modifier: Optional[str] = None


@dataclass
class BindsFile:
    preset_name: str
    major_version: int
    minor_version: int
    bindings: dict[str, Binding] = field(default_factory=dict)

    def get(self, action: str) -> Optional[Binding]:
        return self.bindings.get(action)

    def has(self, action: str) -> bool:
        b = self.bindings.get(action)
        return b is not None and b.key != ""


def _extract_key(node: ET.Element) -> tuple[str, Optional[str]]:
    """
    Return (key, modifier) from a Primary/Secondary node. If the device is
    not Keyboard or the key is blank, returns ('', None).
    """
    device = node.attrib.get("Device", "")
    key = node.attrib.get("Key", "")
    if device != "Keyboard" or not key:
        return "", None
    mod = None
    mod_node = node.find("Modifier")
    if mod_node is not None and mod_node.attrib.get("Device") == "Keyboard":
        mod_key = mod_node.attrib.get("Key", "")
        if mod_key:
            mod = mod_key
    return key, mod


def parse_binds(path: str | Path) -> BindsFile:
    """Parse a .binds file. Raises on malformed XML."""
    tree = ET.parse(path)
    root = tree.getroot()
    preset_name = root.attrib.get("PresetName", "")
    try:
        major = int(root.attrib.get("MajorVersion", "0"))
        minor = int(root.attrib.get("MinorVersion", "0"))
    except ValueError:
        major, minor = 0, 0

    bindings: dict[str, Binding] = {}
    for action_node in root:
        action = action_node.tag
        # Skip non-action top-level elements (KeyboardLayout, etc).
        if not list(action_node):
            continue
        primary = action_node.find("Primary")
        secondary = action_node.find("Secondary")
        key, mod = "", None
        sec_key, sec_mod = None, None
        if primary is not None:
            key, mod = _extract_key(primary)
        if secondary is not None:
            sec_key, sec_mod = _extract_key(secondary)
            if not sec_key:
                sec_key, sec_mod = None, None
        if not key and not sec_key:
            continue
        # If primary isn't keyboard but secondary is, promote secondary.
        if not key and sec_key:
            key, mod = sec_key, sec_mod
            sec_key, sec_mod = None, None
        bindings[action] = Binding(
            action=action,
            key=key,
            modifier=mod,
            secondary_key=sec_key,
            secondary_modifier=sec_mod,
        )

    return BindsFile(
        preset_name=preset_name,
        major_version=major,
        minor_version=minor,
        bindings=bindings,
    )
