"""Fuel-scoop max-rate lookup by Loadout module item string.

Rates live in `data/fuel_scoops.json` (EDCD/coriolis-data, t/s — see the
file's _source header). The scoop_refuel step uses the equipped scoop's max
rate as the standoff yardstick: observed rate >= standoff_frac * max means
"close enough" (spec 2026-06-06-scoop-refuel-design §4.1).
"""

from __future__ import annotations

import importlib.resources as pkg_resources
import json
import re
from functools import lru_cache
from typing import Optional

# Loadout writes e.g. "int_fuelscoop_size6_class5"; class1=E .. class5=A
# (same numbering convention as the FSD lookup in fsd/math.py).
_ITEM_RE = re.compile(
    r"int_fuelscoop_size(?P<size>\d+)_class(?P<rat>\d+)", re.IGNORECASE
)

_RATING_NUM_TO_LETTER = {1: "E", 2: "D", 3: "C", 4: "B", 5: "A"}


@lru_cache(maxsize=1)
def _load_table() -> dict[tuple[int, str], float]:
    text = (
        pkg_resources.files("ed_autojump")
        .joinpath("data/fuel_scoops.json")
        .read_text(encoding="utf-8")
    )
    raw = json.loads(text)
    return {(s["size"], s["rating"]): s["rate_t_s"] for s in raw["scoops"]}


def scoop_max_rate_t_s(item: str) -> Optional[float]:
    """Max scoop rate in tonnes/s for a Loadout item string, or None when
    the string isn't a recognizable scoop (caller treats None as a fail-safe
    skip — never guess a rate)."""
    m = _ITEM_RE.fullmatch(item.strip().lower())
    if m is None:
        return None
    rating = _RATING_NUM_TO_LETTER.get(int(m.group("rat")))
    if rating is None:
        return None
    return _load_table().get((int(m.group("size")), rating))
