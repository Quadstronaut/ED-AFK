"""Danger-class refusal + regression guards for deleted legacy landmines.

2026-06-06 purge: the escape-macro tests (and their "Every FSDJump produces
a PitchUp + throttle pair" fiction) are gone with perform_star_escape. The
LIVE arrival behavior is procedures/arrival.toml — covered by the flow
tests, not here.
"""

from __future__ import annotations

import pytest

from ed_autojump.executor.jump import should_refuse_target
from ed_core.journal import parse_event


# --- danger-class refusal (req 3 + req 7 defense in depth) ----------------


@pytest.mark.parametrize("cls", ["DA", "D", "N", "H", "W", "WC", "WN"])
def test_should_refuse_target_dangerous(cls: str):
    target = parse_event(
        f'{{"timestamp":"2026-01-10T03:00:00Z","event":"FSDTarget",'
        f'"Name":"X","SystemAddress":1,"StarClass":"{cls}","RemainingJumpsInRoute":1}}'
    )
    assert should_refuse_target(target) is True


@pytest.mark.parametrize("cls", ["K", "G", "B", "F", "O", "A", "M"])
def test_should_refuse_target_safe(cls: str):
    target = parse_event(
        f'{{"timestamp":"2026-01-10T03:00:00Z","event":"FSDTarget",'
        f'"Name":"X","SystemAddress":1,"StarClass":"{cls}","RemainingJumpsInRoute":1}}'
    )
    assert should_refuse_target(target) is False


# --- deleted-landmine regression guards ------------------------------------
# Unwired-but-present code eventually gets wired (hold_alignment proved it,
# twice nearly the wrong way). These keep the landmines from coming back.


def test_handle_start_jump_is_deleted():
    """SetSpeedZero-on-StartJump would stall jumps — the FSD needs FULL
    throttle start to finish; ED auto-dethrottles on arrival."""
    import ed_autojump.executor as executor
    import ed_autojump.executor.jump as jump_mod
    assert not hasattr(jump_mod, "handle_start_jump")
    assert "handle_start_jump" not in executor.__all__


def test_perform_star_escape_is_deleted():
    """Fixed-time pitch then throttle with no off-screen confirmation —
    throttles toward the star; violates pitch-star-first. Live escape is
    arrival.toml's orbit flow."""
    import ed_autojump.executor as executor
    import ed_autojump.executor.jump as jump_mod
    assert not hasattr(jump_mod, "perform_star_escape")
    assert not hasattr(jump_mod, "DEFAULT_CLASS_PITCH_S")
    assert "perform_star_escape" not in executor.__all__


def test_perform_honk_is_deleted():
    """Legacy honk macro with unverified combat-mode retry + timed resolve
    gate. The live honk is honk.toml's event-gated hold_until_event."""
    import ed_autojump.executor as executor
    assert "perform_honk" not in executor.__all__
    import importlib
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("ed_autojump.executor.honk")


# --- danger journal replay --------------------------------------------------


def test_danger_journal_all_refused(danger_journal):
    """All five targets in danger_journal except K class get refused."""
    from ed_core.journal import JournalTail
    from ed_core.journal.events import FSDTarget

    tail = JournalTail(danger_journal.parent)
    refused = 0
    accepted = 0
    for ev in tail.replay_file(danger_journal):
        if isinstance(ev, FSDTarget):
            if should_refuse_target(ev):
                refused += 1
            else:
                accepted += 1
    assert refused == 4
    assert accepted == 1
