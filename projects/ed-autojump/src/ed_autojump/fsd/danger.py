"""
Star-class danger filter.

Defense in depth: the planner filter rejects danger-class legs at routing
time; this module is consulted again immediately before FSD engage
(SPEC §9.5.2) and on `FSDTarget` arrival.

Source: SPEC §8.4, derived from EDCD/EDDN canonical StarClass values and
Frontier Player Journal manual v32.
"""

from __future__ import annotations


# KGBFOAM — main-sequence, scoopable.
SCOOPABLE_CLASSES: frozenset[str] = frozenset(["O", "B", "A", "F", "G", "K", "M"])

# Non-scoopable but low-hazard (transit only — must not be a sole star).
TRANSIT_CLASSES: frozenset[str] = frozenset(
    ["L", "T", "Y", "S", "MS", "C", "AeBe", "TTS"]
)

# Hazardous exclusion-zone classes (white dwarfs, neutron, black hole, Wolf-Rayet).
DEFAULT_DANGER_CLASSES: frozenset[str] = frozenset(
    [
        # White dwarf variants
        "D", "DA", "DAB", "DAO", "DAZ", "DAV",
        "DB", "DBZ", "DBV",
        "DO", "DOV",
        "DQ", "DC", "DCV", "DX",
        # Neutron
        "N",
        # Black hole
        "H",
        # Wolf-Rayet variants
        "W", "WC", "WN", "WNC", "WO",
    ]
)


def is_dangerous(star_class: str, danger_set: frozenset[str] | tuple[str, ...] | None = None) -> bool:
    """
    True if `star_class` is in the danger set.

    The default set covers white dwarfs (`D`/`DA`/…), neutron (`N`), black
    hole (`H`), and Wolf-Rayet (`W*`). Callers may pass a custom set from
    `config.routing.danger_classes` to override.
    """
    if danger_set is None:
        danger_set = DEFAULT_DANGER_CLASSES
    return star_class in danger_set


def is_scoopable(star_class: str) -> bool:
    """KGBFOAM check."""
    return star_class in SCOOPABLE_CLASSES
