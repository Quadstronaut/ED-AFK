"""Refuel-on-star mode — approach, scoop to full, peel off on assist, orient.

Drives perform_refuel_on_star deterministically: RecordingSender for key
assertions, injected single-pass FuelScoop iterators, a fake engage_assist that
records WHEN it was called (relative to the recorded key presses), a fake
compass reader for the optional orient step, and lambda clock/sleeper so nothing
sleeps or touches the game.

The mechanism under test (confirmed against the user's real logs):
  - Approach throttle -> FuelScoop events begin.
  - When `Scooped` plateaus (newest ~= previous) -> ONE SetSpeedZero.
  - When `Total` reaches capacity (no discrete "full" event) -> tank full.
  - Peel off: assist -> TargetNextRouteSystem -> SetSpeed100, then align.
"""

from __future__ import annotations

from pathlib import Path

from ed_autojump.executor.refuel import RefuelOutcome, perform_refuel_on_star
from ed_autojump.journal.events import FuelScoop
from ed_autojump.keys import RecordingSender, parse_binds
from ed_autojump.vision.compass import CompassRead


def _binds():
    src = Path(__file__).parent.parent / "src/ed_autojump/binds/ED-AFK.4.2.binds"
    return parse_binds(src)


def _scoop(scooped: float, total: float) -> FuelScoop:
    """Build a FuelScoop event via the pydantic model + journal aliases."""
    return FuelScoop.model_validate(
        {
            "timestamp": "2026-05-24T03:00:55Z",
            "event": "FuelScoop",
            "Scooped": scooped,
            "Total": total,
        }
    )


class _CenteredReader:
    """Fake CompassReader: every read is centred + in front, so
    align_to_target converges to aligned=True on the first measurement."""

    def read(self, frame):  # noqa: ANN001 - frame ignored
        return CompassRead(
            found=True, offset_x=0.0, offset_y=0.0, in_front=True, confidence=1.0
        )


class _AssistRecorder:
    """Fake engage_assist that records the sender's action count at call time,
    so a test can prove the assist fired AFTER the scoop and BEFORE the
    TargetNextRouteSystem peel-off press."""

    def __init__(self, sender: RecordingSender):
        self._sender = sender
        self.called = False
        self.actions_at_call: list[str] | None = None

    def __call__(self) -> None:
        self.called = True
        self.actions_at_call = list(self._sender.actions())


# --- happy path: plateau -> full -> assist -> depart -> align -------------


def test_refuel_full_fill_plateau_assist_then_orient():
    sender = RecordingSender(_binds())
    assist = _AssistRecorder(sender)
    # Capacity 32.0 (the Mandalay). Scooped climbs 2 -> 5.003 -> 5.001 (plateau
    # on the 3rd event), Total climbs to 32.0 (full) on the last event.
    events = iter(
        [
            _scoop(2.000, 22.0),
            _scoop(5.003, 27.0),  # rate climbing
            _scoop(5.001, 31.0),  # plateau: |5.001-5.003| <= eps -> SetSpeedZero
            _scoop(5.002, 32.0),  # Total hits capacity -> full
        ]
    )
    out = perform_refuel_on_star(
        sender,
        events,
        fuel_capacity_t=32.0,
        initial_fuel_t=15.0,
        engage_assist=assist,
        compass_reader=_CenteredReader(),
        compass_capture=lambda: object(),
        sleeper=lambda _s: None,
        clock=lambda: 0.0,
    )

    assert isinstance(out, RefuelOutcome)
    assert out.saw_scoop is True
    assert out.was_full is False
    assert out.throttle_cut is True
    assert out.final_fuel_t == 32.0
    assert out.assist_engaged is True
    assert out.aligned is True

    actions = sender.actions()
    # Approach throttle pressed first.
    assert actions[0] == "SetSpeed75"
    # Exactly one SetSpeedZero (the plateau cut), no double-cut.
    assert actions.count("SetSpeedZero") == 1
    # Peel-off presses present and in order.
    assert "TargetNextRouteSystem" in actions
    assert "SetSpeed100" in actions
    assert actions.index("SetSpeed100") > actions.index("TargetNextRouteSystem")

    # Assist fired after the scoop (SetSpeedZero already recorded) and BEFORE the
    # TargetNextRouteSystem press (not yet recorded at call time).
    assert assist.called is True
    assert "SetSpeedZero" in assist.actions_at_call
    assert "TargetNextRouteSystem" not in assist.actions_at_call


# --- already full: skip approach + scoop, still peel off ------------------


def test_refuel_already_full_skips_to_peeloff():
    sender = RecordingSender(_binds())
    assist = _AssistRecorder(sender)
    out = perform_refuel_on_star(
        sender,
        iter([]),  # never read — we short-circuit before the loop
        fuel_capacity_t=32.0,
        initial_fuel_t=32.0,  # already full
        engage_assist=assist,
        sleeper=lambda _s: None,
        clock=lambda: 0.0,
    )

    assert out.was_full is True
    assert out.saw_scoop is False
    assert out.throttle_cut is False
    assert out.assist_engaged is True
    assert out.aligned is None  # no compass wired
    assert "already full" in out.notes

    actions = sender.actions()
    # No approach throttle, no SetSpeedZero — we skipped straight to peel-off.
    assert "SetSpeed75" not in actions
    assert "SetSpeedZero" not in actions
    # But we still peeled off.
    assert "TargetNextRouteSystem" in actions
    assert "SetSpeed100" in actions


# --- no scoop events: graceful depart, never strand the ship --------------


def test_refuel_no_scoop_events_departs_gracefully():
    sender = RecordingSender(_binds())
    assist = _AssistRecorder(sender)
    out = perform_refuel_on_star(
        sender,
        iter([]),  # not full, but the stream yields nothing scoopable
        fuel_capacity_t=32.0,
        initial_fuel_t=10.0,
        engage_assist=assist,
        sleeper=lambda _s: None,
        clock=lambda: 0.0,
    )

    assert out.was_full is False
    assert out.saw_scoop is False
    assert out.throttle_cut is False
    assert out.assist_engaged is True
    assert "no scoop events" in out.notes

    actions = sender.actions()
    # Approach was attempted...
    assert actions[0] == "SetSpeed75"
    # ...but no plateau cut, and we still peel off.
    assert "SetSpeedZero" not in actions
    assert "TargetNextRouteSystem" in actions
    assert "SetSpeed100" in actions


# --- plateau detection fires exactly once ---------------------------------


def test_refuel_plateau_two_equal_scooped_cuts_throttle_once():
    sender = RecordingSender(_binds())
    assist = _AssistRecorder(sender)
    # Two equal Scooped values back-to-back = plateau. Then several MORE equal
    # ticks before full — must NOT cut throttle again. Total never reaches 32.0
    # so the stream ends and we peel off.
    events = iter(
        [
            _scoop(5.000, 20.0),
            _scoop(5.000, 25.0),  # plateau -> single SetSpeedZero
            _scoop(5.000, 30.0),  # still plateaued, must not re-cut
            _scoop(5.000, 31.0),
        ]
    )
    out = perform_refuel_on_star(
        sender,
        events,
        fuel_capacity_t=32.0,
        initial_fuel_t=10.0,
        engage_assist=assist,
        sleeper=lambda _s: None,
        clock=lambda: 0.0,
    )

    assert out.throttle_cut is True
    assert out.saw_scoop is True
    assert out.final_fuel_t == 31.0  # last seen, never reached 32.0
    # Exactly ONE SetSpeedZero across the whole run.
    assert sender.actions().count("SetSpeedZero") == 1


# --- live-tunable approach knob honoured -----------------------------------


def test_refuel_approach_throttle_knob_is_used():
    sender = RecordingSender(_binds())
    assist = _AssistRecorder(sender)
    perform_refuel_on_star(
        sender,
        iter([_scoop(5.0, 32.0)]),
        fuel_capacity_t=32.0,
        initial_fuel_t=10.0,
        engage_assist=assist,
        approach_throttle="SetSpeed100",  # override the one live knob
        sleeper=lambda _s: None,
        clock=lambda: 0.0,
    )
    # The first press is the overridden approach throttle.
    assert sender.actions()[0] == "SetSpeed100"
