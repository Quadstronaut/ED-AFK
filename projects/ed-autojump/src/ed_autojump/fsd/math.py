"""
FSD fuel formula + max-range solver.

Per SPEC §8.1:

    fuel_cost = (LC * 0.001) * (ship_mass * dist / opt_mass) ^ PC
    max_range = (min(maxfuel, fuel) / (LC * 0.001)) ^ (1/PC) * opt_mass / ship_mass

where ship_mass = UnladenMass + FuelLevel + Cargo.

Constants live in `data/fsd_modules.json` (borrowed from coriolis-data, MIT).
"""

from __future__ import annotations

import importlib.resources as pkg_resources
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional


@dataclass(frozen=True)
class FsdSpec:
    """One FSD module's hyperspace constants."""

    cls: int
    rating: str
    linear_constant: int
    power_constant: float
    opt_mass: float
    max_fuel_per_jump: float


_ITEM_RE = re.compile(
    r"int_hyperdrive(?:_overcharge)?_size(?P<cls>\d+)_class(?P<rat>\d+)",
    re.IGNORECASE,
)

_RATING_NUM_TO_LETTER = {1: "E", 2: "D", 3: "C", 4: "B", 5: "A"}


@lru_cache(maxsize=1)
def load_modules() -> list[FsdSpec]:
    """Load FSD module constants from packaged data file."""
    text = (
        pkg_resources.files("ed_autojump")
        .joinpath("data/fsd_modules.json")
        .read_text(encoding="utf-8")
    )
    raw = json.loads(text)
    return [
        FsdSpec(
            cls=m["class"],
            rating=m["rating"],
            linear_constant=m["linear_constant"],
            power_constant=m["power_constant"],
            opt_mass=m["opt_mass"],
            max_fuel_per_jump=m["max_fuel_per_jump"],
        )
        for m in raw["modules"]
    ]


def fsd_spec_for(cls: int, rating: str) -> FsdSpec:
    """Look up an FSD spec by class + letter rating."""
    rating = rating.upper()
    for spec in load_modules():
        if spec.cls == cls and spec.rating == rating:
            return spec
    raise KeyError(f"no FSD spec for class {cls} rating {rating!r}")


def fsd_spec_from_item(item: str) -> Optional[FsdSpec]:
    """
    Parse `int_hyperdrive_size5_class5` (or `_overcharge_` variant) and look
    up the constants. SCO FSDs use the same hyperspace formula as the
    matching standard class+rating, per SPEC §8.2.
    """
    m = _ITEM_RE.search(item)
    if m is None:
        return None
    cls = int(m.group("cls"))
    rating_num = int(m.group("rat"))
    rating = _RATING_NUM_TO_LETTER.get(rating_num)
    if rating is None:
        return None
    return fsd_spec_for(cls, rating)


def fsd_spec_from_loadout(loadout) -> Optional[FsdSpec]:
    """Find the FrameShiftDrive module in a Loadout event and resolve it."""
    for mod in getattr(loadout, "modules", []):
        if mod.item.startswith("int_hyperdrive"):
            return fsd_spec_from_item(mod.item)
    return None


def fuel_cost(spec: FsdSpec, ship_mass: float, distance_ly: float) -> float:
    """
    Tonnes of fuel for one jump of `distance_ly` at `ship_mass`.

    Per SPEC §8.1: fuel = (LC/1000) * (m * d / opt) ^ PC.
    """
    lc = spec.linear_constant / 1000.0
    return lc * (ship_mass * distance_ly / spec.opt_mass) ** spec.power_constant


def max_jump_range(spec: FsdSpec, ship_mass: float, fuel: float) -> float:
    """
    Maximum jump distance in LY at current ship_mass and fuel.

    Solves the fuel formula for `dist`. Caps fuel at the FSD's
    `max_fuel_per_jump` (the hard ceiling on per-jump consumption).
    """
    usable = min(spec.max_fuel_per_jump, fuel)
    lc = spec.linear_constant / 1000.0
    return (usable / lc) ** (1.0 / spec.power_constant) * spec.opt_mass / ship_mass
