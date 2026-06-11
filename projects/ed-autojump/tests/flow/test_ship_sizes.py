"""Tests for ship_sizes module and FlowRunner._current_ship latch.

Covers:
  - pitch_s_for_ship for known sizes (S/M/L anchors)
  - case-insensitive lookup
  - unknown model falls back to 4.0 (MEDIUM default)
  - None falls back to 4.0 (MEDIUM default)
  - LoadGame event latches _current_ship
  - Loadout event latches _current_ship
"""

from types import SimpleNamespace

import pytest

from ed_autojump.ship_sizes import pitch_s_for_ship, size_for_ship
from ed_autojump.flow.dispatcher import FlowRunner
from tests.flow import FakeSender


# ---------------------------------------------------------------------------
# pitch_s_for_ship — functional checks
# ---------------------------------------------------------------------------

def test_mandalay_is_medium():
    assert pitch_s_for_ship('mandalay') == 4.0


def test_type9_is_large():
    assert pitch_s_for_ship('type9') == 7.0


def test_sidewinder_is_small():
    assert pitch_s_for_ship('sidewinder') == 3.0


def test_case_insensitive():
    assert pitch_s_for_ship('MANDALAY') == 4.0


def test_unknown_model_defaults_to_medium():
    assert pitch_s_for_ship('foobar') == 4.0


def test_none_defaults_to_medium():
    assert pitch_s_for_ship(None) == 4.0


def test_size_for_ship_known():
    assert size_for_ship('mandalay') == 'M'
    assert size_for_ship('type9') == 'L'
    assert size_for_ship('sidewinder') == 'S'


def test_size_for_ship_unknown_returns_none():
    assert size_for_ship('foobar') is None


def test_size_for_ship_none_returns_none():
    assert size_for_ship(None) is None


# ---------------------------------------------------------------------------
# FlowRunner._current_ship latch
# ---------------------------------------------------------------------------

def _ev(name, **fields):
    return SimpleNamespace(event=name, **fields)


def _minimal_runner():
    return FlowRunner(
        procedures={},
        sender=FakeSender(),
        clock=lambda: 0.0,
        sleeper=lambda s: None,
        status_supplier=lambda: None,
    )


def test_loadout_event_sets_current_ship():
    r = _minimal_runner()
    assert r._current_ship is None
    r._on_tail_event(_ev("Loadout", ship="Mandalay",
                         fuel_capacity=SimpleNamespace(main=32.0),
                         modules=[]))
    assert r._current_ship == 'mandalay'


def test_loadgame_event_sets_current_ship():
    r = _minimal_runner()
    assert r._current_ship is None
    r._on_tail_event(_ev("LoadGame", ship="type9",
                         commander="CMDR Test"))
    assert r._current_ship == 'type9'


def test_current_ship_lowercased():
    r = _minimal_runner()
    r._on_tail_event(_ev("Loadout", ship="SIDEWINDER",
                         fuel_capacity=SimpleNamespace(main=2.0),
                         modules=[]))
    assert r._current_ship == 'sidewinder'


def test_loadout_overrides_loadgame():
    """Loadout fires after LoadGame and should win."""
    r = _minimal_runner()
    r._on_tail_event(_ev("LoadGame", ship="sidewinder", commander="CMDR Test"))
    r._on_tail_event(_ev("Loadout", ship="Mandalay",
                         fuel_capacity=SimpleNamespace(main=32.0),
                         modules=[]))
    assert r._current_ship == 'mandalay'


def test_loadgame_without_ship_does_not_clear():
    """LoadGame with no Ship field (optional) must not overwrite an existing latch."""
    r = _minimal_runner()
    r._on_tail_event(_ev("Loadout", ship="Mandalay",
                         fuel_capacity=SimpleNamespace(main=32.0),
                         modules=[]))
    r._on_tail_event(_ev("LoadGame", commander="CMDR Test"))  # no ship field
    assert r._current_ship == 'mandalay'
