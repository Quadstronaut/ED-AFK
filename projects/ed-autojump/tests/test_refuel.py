"""Refuel-on-star mode — go TO the star, scoop to full, leave, orient.

Drives perform_refuel_on_star deterministically: RecordingSender for key
assertions, injected event iterators, a fake compass reader for the optional
orient step, and lambda clock/sleeper so nothing sleeps or touches the game.
"""

from __future__ import annotations

from pathlib import Path

from ed_autojump.executor.refuel import (
    DEFAULT_DEPART_S,
    DEFAULT_SCOOP_TIMEOUT_S,
    RefuelOutcome,
    perform_refuel_on_star,
)
from ed_autojump.executor.scoop import ScoopResult
from ed_autojump.journal import parse_event
from ed_autojump.keys import RecordingSender, parse_binds
from ed_autojump.vision.compass import CompassRead


def _binds():
    src = Path(__file__).parent.parent / "src/ed_autojump/binds/ED-AFK.4.2.binds"
    return parse_binds(src)


class _CenteredReader:
    """Fake CompassReader: every read is centred + in front, so
    align_to_target converges to aligned=True on the first measurement."""

    def read(self, frame):  # noqa: ANN001 - frame ignored
        return CompassRead(
            found=True, offset_x=0.0, offset_y=0.0, in_front=True, confidence=1.0
        )


def _full_scoop_events():
    """A single FuelScoop event that already clears 64 * 0.98 = 62.72 t."""
    return [
        parse_event(
            '{"timestamp":"2026-05-24T03:00:55Z","event":"FuelScoop",'
            '"Scooped":40.0,"Total":63.5}'
        )
    ]


# --- happy path: full fill + compass orient -------------------------------


def test_refuel_full_fill_with_compass_orients():
    sender = RecordingSender(_binds())
    out = perform_refuel_on_star(
        sender,
        iter(_full_scoop_events()),
        fuel_capacity_t=64.0,
        initial_fuel_t=15.0,
        compass_reader=_CenteredReader(),
        compass_capture=lambda: object(),
        sleeper=lambda _s: None,
        clock=lambda: 0.0,
    )

    assert isinstance(out, RefuelOutcome)
    # Scoop completed -> propagated verbatim.
    assert out.scoop.result == ScoopResult.COMPLETED
    assert out.departed is True
    # Compass wired -> align ran and converged on the centred reader.
    assert out.aligned is True

    actions = sender.actions()
    # Star locked for approach AND re-targeted for depart => 2 presses.
    assert actions.count("TargetNextRouteSystem") == 2
    # Full throttle on depart.
    assert "SetSpeed100" in actions
    # Approach speed (phase 1) precedes the first route re-target (depart).
    first_retarget = actions.index("TargetNextRouteSystem")
    second_retarget = actions.index("TargetNextRouteSystem", first_retarget + 1)
    assert actions.index("SetSpeed100") > second_retarget


# --- no-compass path: align skipped, still departs ------------------------


def test_refuel_no_compass_skips_align_but_departs():
    sender = RecordingSender(_binds())
    out = perform_refuel_on_star(
        sender,
        iter(_full_scoop_events()),
        fuel_capacity_t=64.0,
        initial_fuel_t=15.0,
        # no compass_reader / compass_capture
        sleeper=lambda _s: None,
        clock=lambda: 0.0,
    )

    assert out.scoop.result == ScoopResult.COMPLETED
    assert out.departed is True
    assert out.aligned is None
    assert "orientation skipped" in out.notes
    assert "SetSpeed100" in sender.actions()


# --- heat-abort path: inner ScoopOutcome propagates -----------------------


def test_refuel_propagates_scoop_heat_abort():
    sender = RecordingSender(_binds())
    # Heat above the backoff threshold on the first probe -> HEAT_ABORT before
    # the fuel event is consumed.
    ev = parse_event(
        '{"timestamp":"2026-05-24T03:00:55Z","event":"FuelScoop",'
        '"Scooped":1.0,"Total":16.0}'
    )
    heats = iter([0.95])
    out = perform_refuel_on_star(
        sender,
        iter([ev]),
        fuel_capacity_t=64.0,
        initial_fuel_t=15.0,
        heat_supplier=lambda: next(heats, None),
        sleeper=lambda _s: None,
        clock=lambda: 0.0,
    )

    # Heat abort propagates through unchanged...
    assert out.scoop.result == ScoopResult.HEAT_ABORT
    # ...and we STILL depart so the ship leaves the corona.
    assert out.departed is True
    actions = sender.actions()
    assert actions.count("TargetNextRouteSystem") == 2
    assert "SetSpeed100" in actions


# --- defaults pinned ------------------------------------------------------


def test_refuel_defaults_are_generous():
    # The user said "don't care how long it takes" -> generous scoop budget,
    # and "wait like 5 seconds" -> 5 s depart.
    assert DEFAULT_SCOOP_TIMEOUT_S == 600.0
    assert DEFAULT_DEPART_S == 5.0
