"""Sanity checks for the ships.json data file (Coriolis.io export)."""

from __future__ import annotations

import importlib.resources as pkg_resources
import json


def _load_ships() -> list[dict]:
    ref = pkg_resources.files("ed_core.data").joinpath("ships.json")
    with ref.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    return payload["ships"]


def test_ships_data_loads_and_count():
    ships = _load_ships()
    assert len(ships) > 30, f"Expected >30 ships, got {len(ships)}"


def test_ships_data_has_required_keys():
    ships = _load_ships()
    required = {"ship", "price", "sz", "crw", "mlf", "unl_mass", "unl_jump",
                "spd", "bst", "shd", "arm", "hrd", "fuel", "crgo", "psgr", "hardpoints"}
    for entry in ships:
        missing = required - entry.keys()
        assert not missing, f"Ship {entry.get('ship')} missing keys: {missing}"


def test_ships_data_unl_jump_positive():
    """All ships must have a positive unladen jump range (basic sanity)."""
    ships = _load_ships()
    for entry in ships:
        assert entry["unl_jump"] > 0, f"Ship {entry['ship']} has unl_jump <= 0"


def test_ships_data_no_duplicate_names():
    ships = _load_ships()
    names = [s["ship"] for s in ships]
    assert len(names) == len(set(names)), "Duplicate ship names found"
