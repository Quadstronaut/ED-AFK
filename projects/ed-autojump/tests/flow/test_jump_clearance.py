"""step_engage_jump_clearance charge-aware poll loop (LIVE FIX 2026-07-06).

Run 004703 sabotage, pinned: the ~15s hyperspace spool shows NEITHER commit
signal (witchspace / fsd_jump bit 30), so the original 12-poll x 0.8s window
verdicted every HEALTHY jump "obscured" and the C4 move pitched the ship off
its own live charge. fsd_charging is the took-signal now:

  no charge within max_jump_polls  -> ED refused the press -> C4 move edge
  charging                         -> wait for commit (max_charge_polls ceiling)
  charge drops without commit      -> grace poll -> C4 move edge
  charge outlives the ceiling      -> False (outer retry re-orients; NEVER
                                     pitch off a live charge)

Pure-Python — no game, no CV. The rig advances its FSD phase on each SLEEP
(one sleep per poll), because the step reads status twice per poll (commit
check + charge check) and a per-call script would desync.
"""

from types import SimpleNamespace

from ed_core.flow.context import StepContext
from ed_autojump.flow.steps import STEP_REGISTRY
from tests.flow import FakeSender


class _FsdRig:
    """FSD phase scripted per POLL: 'idle' / 'charging' / 'jump'.

    The sleeper is the poll clock — each ctx.sleeper() call advances one
    phase slot; the final phase holds forever."""

    def __init__(self, phases):
        self.phases = list(phases)
        self.polls = 0

    def sleep(self, _s):
        self.polls += 1

    def status(self):
        phase = self.phases[min(self.polls, len(self.phases) - 1)]
        return SimpleNamespace(
            docked=False, fsd_charging=(phase == "charging"),
            fsd_cooldown=False, fsd_mass_locked=False, overheating=False,
            fsd_jump=(phase == "jump"),
        )


def _ctx(sender, phases, logs=None):
    rig = _FsdRig(phases)
    return StepContext(
        sender=sender,
        sleeper=rig.sleep,
        status_supplier=rig.status,
        ship_supplier=lambda: "mandalay",
        record=(lambda kind, payload: logs.append((kind, payload)))
        if logs is not None else None,
    )


def test_healthy_charge_longer_than_old_window_commits():
    """THE 2026-07-06 REGRESSION: a healthy ~15s spool (19 charging polls,
    far past the old 12-poll verdict) must be WAITED OUT to the commit —
    no pitch, no extra presses, one attempt."""
    sender = FakeSender()
    ctx = _ctx(sender, ["idle"] + ["charging"] * 19 + ["jump"])
    assert STEP_REGISTRY["engage_jump_clearance"](ctx) is True
    assert sender.actions() == ["SetSpeed100", "Hyperspace"]   # no move ever


def test_no_charge_is_the_obstructed_edge():
    """Press refused (no charge ever) -> C4 move (pitch + burn) then
    re-press; ceiling after max_clear_attempts -> False."""
    sender = FakeSender()
    logs = []
    ctx = _ctx(sender, ["idle"], logs=logs)
    assert STEP_REGISTRY["engage_jump_clearance"](
        ctx, max_jump_polls=3, max_clear_attempts=2) is False
    acts = sender.actions()
    assert acts.count("Hyperspace") == 2            # re-pressed once per attempt
    assert acts.count("PitchDownButton") == 2       # moved after each refusal
    assert ("EngageJumpClearanceAborted",
            {"reason": "obstruction_ceiling", "attempts": 2}) in logs


def test_charge_stuck_fails_without_pitch():
    """ALIGN hold / wedged FSD: charge outlives the ceiling -> False with
    reason charge_stuck and NO directional press — never pitch off a live
    charge (the outer retry re-orients instead)."""
    sender = FakeSender()
    logs = []
    ctx = _ctx(sender, ["idle"] + ["charging"] * 500, logs=logs)
    assert STEP_REGISTRY["engage_jump_clearance"](
        ctx, max_charge_polls=10) is False
    assert "PitchDownButton" not in sender.actions()
    assert sender.actions().count("Hyperspace") == 1
    assert any(k == "EngageJumpClearanceAborted"
               and p.get("reason") == "charge_stuck" for k, p in logs)


def test_charge_dropped_goes_to_move_edge():
    """Charge seen then dropped without commit -> grace poll -> C4 move."""
    sender = FakeSender()
    logs = []
    ctx = _ctx(sender, ["idle"] + ["charging"] * 4 + ["idle"] * 500, logs=logs)
    assert STEP_REGISTRY["engage_jump_clearance"](
        ctx, max_clear_attempts=1) is False
    assert "PitchDownButton" in sender.actions()
    assert any(k == "EngageJumpClearanceChargeDropped" for k, _ in logs)


def test_commit_during_grace_poll_still_wins():
    """Charge drops but the commit lands on the grace poll (status-write vs
    journal-write race) -> True, no move."""
    sender = FakeSender()
    # idle -> charging x3 -> idle (drop seen) -> jump (grace poll)
    ctx = _ctx(sender, ["idle"] + ["charging"] * 3 + ["idle", "jump"])
    assert STEP_REGISTRY["engage_jump_clearance"](ctx) is True
    assert "PitchDownButton" not in sender.actions()
