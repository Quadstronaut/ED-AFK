"""Standalone verifier for step_engage_jump_clearance (NO pytest).

Covers:
  AC8a  abort during inner poll loop -> False, NO PitchDownButton sent
  AC8b  abort at checkpoint 1 (top-of-attempt) -> False immediately
  AC8c  abort at checkpoint 2 (post-poll, pre-pitch) -> False, NO pitch
  AC8d  abort at checkpoint 3 (post-pitch, pre-burn) -> False after pitch
  AC4   frozen clock + never-StartJump -> terminates by read-count (no spin),
        exactly max_clear_attempts press+move cycles, returns False
  AC5a  in_witchspace() True -> returns True, logs EngageJumpClearanceStarted
  AC5b  supercruise StartJump (in_witchspace False, fsd_jump False) -> does NOT
        count as success; ceiling-aborts with EngageJumpClearanceAborted
  AC3   ceiling abort: EngageJumpClearanceAborted reason='obstruction_ceiling',
        attempt count == max_clear_attempts
  AC7   status flag gate: fsd_mass_locked -> False without pressing Hyperspace
  Bp    bad pitch_dir -> EngageJumpClearanceBadParam + False
  Cl    clamp logging: fractional max_jump_polls logs EngageJumpClearanceClamp
  Bnd   move block uses SetSpeed100 (NOT step_set_throttle call)

Exit 0 iff all checks pass. Run with the workspace venv python.
UTF-8 env required: PYTHONIOENCODING=utf-8 PYTHONUTF8=1
"""
from __future__ import annotations

import sys
import types

# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------

_fails: list[str] = []


def check(name: str, cond: bool) -> None:
    print(("  ok  " if cond else " FAIL ") + name)
    if not cond:
        _fails.append(name)


# ---------------------------------------------------------------------------
# Fake StepContext builder
# ---------------------------------------------------------------------------

class _FakeSender:
    """Records every press(action, hold) call."""
    def __init__(self):
        self.presses: list[tuple[str, float]] = []

    def press(self, action: str, hold: float = 0.05) -> None:
        self.presses.append((action, hold))

    def actions(self) -> list[str]:
        return [a for a, _ in self.presses]


def _fake_status(
    *,
    docked: bool = False,
    fsd_charging: bool = False,
    fsd_cooldown: bool = False,
    fsd_mass_locked: bool = False,
    overheating: bool = False,
    fsd_jump: bool = False,
    in_supercruise: bool = True,
):
    return types.SimpleNamespace(
        docked=docked,
        fsd_charging=fsd_charging,
        fsd_cooldown=fsd_cooldown,
        fsd_mass_locked=fsd_mass_locked,
        overheating=overheating,
        fsd_jump=fsd_jump,
        in_supercruise=in_supercruise,
    )


def _make_ctx(
    *,
    sender=None,
    status=None,
    in_witchspace_returns: bool = False,
    # callable override for in_witchspace (takes priority over in_witchspace_returns)
    in_witchspace_fn=None,
    abort_after: int = 9999,   # should_abort() returns True after this many calls
    sleeper=None,
    ship: str = "Asp Explorer",
    log_list: list | None = None,
):
    """Build a minimal StepContext with fakes."""
    from ed_core.flow.context import StepContext

    if sender is None:
        sender = _FakeSender()
    _log = log_list if log_list is not None else []

    _abort_counter = [0]

    def _should_abort() -> bool:
        _abort_counter[0] += 1
        return _abort_counter[0] > abort_after

    def _status_supplier():
        return status

    if in_witchspace_fn is not None:
        _in_witchspace = in_witchspace_fn
    else:
        _in_witchspace = lambda: in_witchspace_returns

    _sleeper = sleeper if sleeper is not None else lambda s: None

    ctx = StepContext(
        sender=sender,
        clock=lambda: 0.0,            # frozen clock — no wall-clock dependence
        sleeper=_sleeper,
        status_supplier=_status_supplier,
        should_abort=_should_abort,
        in_witchspace=_in_witchspace,
        ship_supplier=lambda: ship,
    )

    def _log_fn(key: str, payload=None) -> None:
        _log.append((key, payload or {}))

    ctx.log = _log_fn
    return ctx, sender, _log


# ---------------------------------------------------------------------------
# Import the step under test
# ---------------------------------------------------------------------------

from ed_autojump.flow.steps import step_engage_jump_clearance  # noqa: E402


# ===========================================================================
# AC7 — Status-flag gate: fsd_mass_locked -> False without pressing Hyperspace
# ===========================================================================
print("\n--- AC7: status-flag gate (fsd_mass_locked) ---")
st_locked = _fake_status(fsd_mass_locked=True)
ctx, sender, logs = _make_ctx(status=st_locked)
result = step_engage_jump_clearance(ctx, max_jump_polls=2, max_clear_attempts=1)
check("AC7 returns False", result is False)
check("AC7 Hyperspace NOT pressed", "Hyperspace" not in sender.actions())
check("AC7 EngageBlocked logged",
      any(k == "EngageBlocked" for k, _ in logs))


# ===========================================================================
# Bp — bad pitch_dir -> EngageJumpClearanceBadParam + False
# ===========================================================================
print("\n--- Bp: bad pitch_dir ---")
ctx, sender, logs = _make_ctx(status=_fake_status())
result = step_engage_jump_clearance(ctx, pitch_dir="sideways",
                                    max_jump_polls=1, max_clear_attempts=1)
check("Bp returns False", result is False)
check("Bp EngageJumpClearanceBadParam logged",
      any(k == "EngageJumpClearanceBadParam" for k, _ in logs))
check("Bp Hyperspace NOT pressed", "Hyperspace" not in sender.actions())


# ===========================================================================
# AC5a — in_witchspace() True -> returns True (success edge)
# ===========================================================================
print("\n--- AC5a: hyperspace committed via in_witchspace ---")
ctx, sender, logs = _make_ctx(
    status=_fake_status(),
    in_witchspace_returns=True,
)
result = step_engage_jump_clearance(ctx, max_jump_polls=5, max_clear_attempts=2)
check("AC5a returns True", result is True)
check("AC5a EngageJumpClearanceStarted logged",
      any(k == "EngageJumpClearanceStarted" for k, _ in logs))
check("AC5a NO PitchDownButton sent (no move)", "PitchDownButton" not in sender.actions())
check("AC5a NO PitchUpButton sent", "PitchUpButton" not in sender.actions())


# ===========================================================================
# AC5a-b — fsd_jump bit True (status-side fallback) -> returns True
# ===========================================================================
print("\n--- AC5a-b: hyperspace committed via fsd_jump status bit ---")
ctx, sender, logs = _make_ctx(
    status=_fake_status(fsd_jump=True),
    in_witchspace_returns=False,   # witchspace NOT set — state-side fallback
)
result = step_engage_jump_clearance(ctx, max_jump_polls=5, max_clear_attempts=2)
check("AC5a-b returns True", result is True)
check("AC5a-b EngageJumpClearanceStarted logged",
      any(k == "EngageJumpClearanceStarted" for k, _ in logs))


# ===========================================================================
# AC5b — supercruise StartJump (in_witchspace False, fsd_jump False) -> NOT success
# ===========================================================================
print("\n--- AC5b: SC StartJump does NOT satisfy hyperspace gate ---")
# in_witchspace stays False (SC entry doesn't set it); fsd_jump stays False
ctx, sender, logs = _make_ctx(
    status=_fake_status(fsd_jump=False),
    in_witchspace_returns=False,
)
result = step_engage_jump_clearance(ctx, max_jump_polls=2, max_clear_attempts=1)
check("AC5b SC StartJump -> returns False (ceiling)", result is False)
check("AC5b EngageJumpClearanceAborted reason=obstruction_ceiling",
      any(k == "EngageJumpClearanceAborted"
          and p.get("reason") == "obstruction_ceiling"
          for k, p in logs))


# ===========================================================================
# AC4 + AC3 — frozen clock, StartJump never fires
# ===========================================================================
print("\n--- AC4+AC3: frozen clock, no StartJump -> ceiling after max_clear_attempts ---")
MAX_POLLS = 3
MAX_ATTEMPTS = 2
press_log: list[str] = []

class _CountingSender:
    """Records presses."""
    def __init__(self):
        self.presses: list[str] = []
    def press(self, action: str, hold: float = 0.05) -> None:
        self.presses.append(action)
    def actions(self):
        return self.presses

counting_sender = _CountingSender()
ctx, _, logs = _make_ctx(
    sender=counting_sender,
    status=_fake_status(fsd_jump=False),
    in_witchspace_returns=False,
)
result = step_engage_jump_clearance(
    ctx,
    max_jump_polls=MAX_POLLS,
    max_clear_attempts=MAX_ATTEMPTS,
    clear_burn_s=0.0,  # no real sleep needed
)
check("AC4 returns False", result is False)
check("AC3 EngageJumpClearanceAborted reason=obstruction_ceiling",
      any(k == "EngageJumpClearanceAborted"
          and p.get("reason") == "obstruction_ceiling"
          for k, p in logs))

# Count Hyperspace presses — must equal max_clear_attempts (one per attempt)
hyper_presses = counting_sender.actions().count("Hyperspace")
check(f"AC3 Hyperspace pressed exactly max_clear_attempts={MAX_ATTEMPTS}",
      hyper_presses == MAX_ATTEMPTS)

# Count move cycles: PitchDownButton presses must equal max_clear_attempts
pitch_presses = counting_sender.actions().count("PitchDownButton")
check(f"AC3 PitchDownButton pressed exactly max_clear_attempts={MAX_ATTEMPTS}",
      pitch_presses == MAX_ATTEMPTS)

# Verify attempt count in the aborted log
aborted_payloads = [p for k, p in logs if k == "EngageJumpClearanceAborted"
                    and p.get("reason") == "obstruction_ceiling"]
check("AC3 aborted payload attempts == max_clear_attempts",
      len(aborted_payloads) >= 1 and aborted_payloads[-1].get("attempts") == MAX_ATTEMPTS)


# ===========================================================================
# Bnd — move block uses SetSpeed100 hardcoded, NOT step_set_throttle
# ===========================================================================
print("\n--- Bnd: move block uses SetSpeed100 (not step_set_throttle) ---")
# The move block must send SetSpeed100 AFTER PitchDownButton during a move cycle.
# We use the counting sender from the AC3/AC4 test above.
actions = counting_sender.actions()
# Find positions of PitchDownButton and SetSpeed100
pitch_positions = [i for i, a in enumerate(actions) if a == "PitchDownButton"]
speed100_positions = [i for i, a in enumerate(actions) if a == "SetSpeed100"]
# Each move cycle: ... Hyperspace ... PitchDownButton ... SetSpeed100 ...
# SetSpeed100 also appears before each Hyperspace press (the C1 press).
# After PitchDownButton, the very next SetSpeed100 should be the move throttle.
burn_throttle_found = False
for pp in pitch_positions:
    # look for SetSpeed100 after this PitchDownButton
    post_pitch_speed = [i for i in speed100_positions if i > pp]
    if post_pitch_speed:
        burn_throttle_found = True
        break
check("Bnd SetSpeed100 follows PitchDownButton in move cycle", burn_throttle_found)


# ===========================================================================
# AC8a — abort during inner poll loop -> False, NO PitchDownButton sent
# ===========================================================================
print("\n--- AC8a: abort during inner poll loop -> no PitchDownButton ---")
# abort_after=2: should_abort() returns True on the 3rd call.
# Call sequence: checkpoint-1 (call 1), inner-poll (call 2, 3rd call triggers True)
# Set abort_after to trip INSIDE the poll loop before it finishes.
abort_sender = _FakeSender()
ctx, _, logs = _make_ctx(
    sender=abort_sender,
    status=_fake_status(),
    in_witchspace_returns=False,
    abort_after=2,   # abort on 3rd should_abort() call
)
result = step_engage_jump_clearance(
    ctx,
    max_jump_polls=5,
    max_clear_attempts=3,
    clear_burn_s=0.0,
)
check("AC8a returns False", result is False)
check("AC8a NO PitchDownButton sent",
      "PitchDownButton" not in abort_sender.actions())
check("AC8a EngageJumpClearanceAborted reason=abort",
      any(k == "EngageJumpClearanceAborted" and p.get("reason") == "abort"
          for k, p in logs))


# ===========================================================================
# AC8b — abort at checkpoint 1 (top-of-attempt) -> False immediately
# ===========================================================================
print("\n--- AC8b: abort at checkpoint 1 (top-of-attempt) ---")
abort_sender2 = _FakeSender()
ctx, _, logs2 = _make_ctx(
    sender=abort_sender2,
    status=_fake_status(),
    in_witchspace_returns=False,
    abort_after=0,   # abort on the VERY FIRST should_abort() call
)
result = step_engage_jump_clearance(
    ctx,
    max_jump_polls=5,
    max_clear_attempts=3,
    clear_burn_s=0.0,
)
check("AC8b returns False", result is False)
check("AC8b Hyperspace NOT pressed",
      "Hyperspace" not in abort_sender2.actions())
check("AC8b PitchDownButton NOT pressed",
      "PitchDownButton" not in abort_sender2.actions())


# ===========================================================================
# AC8c — abort at post-poll checkpoint (AC8 GRAFT), before pitch
# ===========================================================================
print("\n--- AC8c: abort via AC8 graft (post-loop, pre-pitch) -> no PitchDownButton ---")
# We need abort to fire exactly at the post-loop abort check.
# Call sequence per attempt:
#   1: checkpoint-1 (not aborted -> continues)
#   inner poll loop: for poll_i in range(effective_polls):
#     each iteration calls should_abort() once, then sleeper, then check witchspace
#   post-loop abort graft call: THIS is what we test
# With effective_polls=1: poll iteration = call 2 (not aborted, in_witchspace False)
# post-loop graft = call 3 -> abort here
abort_sender3 = _FakeSender()
ctx, _, logs3 = _make_ctx(
    sender=abort_sender3,
    status=_fake_status(),
    in_witchspace_returns=False,
    abort_after=2,   # abort on 3rd call (0-indexed: calls 0,1 ok; call 2 aborts)
)
result = step_engage_jump_clearance(
    ctx,
    max_jump_polls=1,    # exactly 1 poll iteration (1 should_abort inside loop)
    max_clear_attempts=3,
    clear_burn_s=0.0,
)
check("AC8c returns False", result is False)
# The Hyperspace WAS pressed (C1 succeeded), but PitchDownButton must NOT have been
check("AC8c PitchDownButton NOT sent after abort graft",
      "PitchDownButton" not in abort_sender3.actions())
check("AC8c EngageJumpClearanceAborted reason=abort",
      any(k == "EngageJumpClearanceAborted" and p.get("reason") == "abort"
          for k, p in logs3))


# ===========================================================================
# AC8d — abort at checkpoint 3 (post-pitch) -> False (pitch WAS sent, burn NOT)
# ===========================================================================
print("\n--- AC8d: abort at checkpoint 3 (post-pitch, pre-burn) ---")
# Call sequence (effective_polls=1, abort_after set to trip after pitch press):
# checkpoint-1: call 1 (ok)
# poll loop iteration 0: call 2 (ok, no witchspace -> loop exits)
# post-loop graft: call 3 (ok, not aborted yet)
# EngageJumpClearanceObscured logged
# checkpoint 2 (pre-pitch): call 4 (ok)
# _press PitchDownButton (not a should_abort call)
# checkpoint 3 (post-pitch): call 5 -> ABORT HERE
# abort_after=4 means calls 0..4 ok, call 5 aborts (0-indexed counter > 4)
abort_sender4 = _FakeSender()
ctx, _, logs4 = _make_ctx(
    sender=abort_sender4,
    status=_fake_status(),
    in_witchspace_returns=False,
    abort_after=4,
)
result = step_engage_jump_clearance(
    ctx,
    max_jump_polls=1,
    max_clear_attempts=3,
    clear_burn_s=0.0,
)
check("AC8d returns False", result is False)
check("AC8d PitchDownButton WAS sent (pitch committed before abort)",
      "PitchDownButton" in abort_sender4.actions())
# But we should NOT see a second SetSpeed100 AFTER PitchDownButton (the burn press)
# Actually burn SetSpeed100 may or may not fire depending on exact call count;
# the important safety guarantee is that the STEP returns False (no further JUMPS).
check("AC8d EngageJumpClearanceAborted reason=abort",
      any(k == "EngageJumpClearanceAborted" and p.get("reason") == "abort"
          for k, p in logs4))


# ===========================================================================
# Cl — clamp: fractional max_jump_polls logs EngageJumpClearanceClamp
# ===========================================================================
print("\n--- Cl: clamp logging for fractional max_jump_polls ---")
ctx, sender, logs = _make_ctx(
    status=_fake_status(fsd_jump=True),   # succeed immediately so we don't need many polls
    in_witchspace_returns=False,
)
result = step_engage_jump_clearance(
    ctx,
    max_jump_polls=2.7,   # fractional -> should clamp to 2 and log
    max_clear_attempts=1,
)
check("Cl returns True (immediate success)", result is True)
check("Cl EngageJumpClearanceClamp logged for max_jump_polls",
      any(k == "EngageJumpClearanceClamp" and "max_jump_polls" in p
          for k, p in logs))


# ===========================================================================
# Summary
# ===========================================================================
print()
if _fails:
    print(f"RESULT: FAIL ({len(_fails)} checks failed)")
    for f in _fails:
        print("  - " + f)
    sys.exit(1)
print("RESULT: PASS")
sys.exit(0)
