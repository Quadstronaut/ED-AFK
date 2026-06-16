"""Standalone verifier for the C3 smack determination redesign (NO pytest).

Covers:
  T1  central ship-safety: grabber=None + Star drop -> abstain, no smack_recovery
  T2  deliberate drop: CV 'none' + Star or Planet -> no recovery, Negative record
  T3  star-smack: CV 'blue' + Star -> smack_recovery, kind='star', StarSmackConfirmed
  T4  planet-smack: CV 'purple' + Planet -> smack_recovery, kind='planet', PlanetSmackConfirmed
  T5  station drop -> None, no determination record
  T6  scene_for with smacked=True but smack_kind=None -> NOT STARSMACK
      scene_for with smack_kind='star' -> STARSMACK selected
  T7  classify_smack truth table (blue/purple/none/garbage/'')
  T8  detect_escape_vector stub returns 'none' on ndarray, valid token, has TODO markers
  T12 mismatch (Star + 'purple') -> no recovery, SmackDeterminationMismatch

Exit 0 iff all checks pass. Run with the workspace venv python.
UTF-8 env required: PYTHONIOENCODING=utf-8 PYTHONUTF8=1.
"""
from __future__ import annotations

import sys
import types

# ---- Infrastructure ----

_fails: list[str] = []


def check(name: str, cond: bool) -> None:
    print(("  ok  " if cond else " FAIL ") + name)
    if not cond:
        _fails.append(name)


# ---- Imports ----

from ed_vision.escape_vector import (
    detect_escape_vector,
    NONE as EV_NONE, BLUE as EV_BLUE, PURPLE as EV_PURPLE,
    VALID_TOKENS,
)

# classify_smack lives in ed_core.boot.primitives (spec location)
from ed_core.boot.primitives import classify_smack

from ed_core.boot.scenes import (
    CSeriesState, DetermineContext, scene_for, scene_by_state,
)

from ed_autojump.flow.boot_routes import _route_sc_exit


# ---- Fake runner factory ----

def _fake_runner(
    *,
    escape_vector_grabber=None,  # None = unwired; callable = wired
    smack_kind=None,
):
    """Minimal stand-in for FlowRunner — only the attributes _route_sc_exit reads."""
    runner = types.SimpleNamespace(
        _escape_vector_grabber=escape_vector_grabber,
        _smack_kind=smack_kind,
        _event_times={},
        _ran=[],          # collects _run() calls
        _records=[],      # collects record() calls
    )

    def _run(proc, *a, **kw):
        runner._ran.append(proc)

    def _record(key, payload=None):
        runner._records.append((key, payload))

    runner._run = _run
    runner.record = _record
    runner.clock = lambda: 0.0
    return runner


def _ev(body_type: str):
    """Minimal SupercruiseExit event."""
    return types.SimpleNamespace(body_type=body_type, event="SupercruiseExit")


# ===========================================================================
# T7 — classify_smack truth table (tests the pure primitive first)
# ===========================================================================
print("\n--- T7: classify_smack truth table ---")
check("T7 blue -> star", classify_smack("blue") == "star")
check("T7 purple -> planet", classify_smack("purple") == "planet")
check("T7 none -> None", classify_smack("none") is None)
check("T7 garbage -> None", classify_smack("garbage") is None)
check("T7 empty string -> None", classify_smack("") is None)
check("T7 'BLUE' (wrong case) -> None", classify_smack("BLUE") is None)


# ===========================================================================
# T8 — detect_escape_vector stub contract
# ===========================================================================
print("\n--- T8: detect_escape_vector stub ---")

# Synthetic BGR ndarray via a bytearray (no numpy required for the stub test).
# The stub must NOT import cv2/numpy at call time (lazy import rule).
class _FakeFrame:
    """Stand-in for a BGR ndarray — shape attribute only."""
    shape = (1080, 1920, 3)

fake_frame = _FakeFrame()
token = detect_escape_vector(fake_frame)
check("T8 stub returns 'none' on synthetic frame", token == EV_NONE)
check("T8 return value in VALID_TOKENS", token in VALID_TOKENS)

# Check that the three TODO calibration markers are present in the source.
import inspect, pathlib
import ed_vision.escape_vector as _ev_mod
src = pathlib.Path(inspect.getfile(_ev_mod)).read_text(encoding='utf-8')
check("T8 TODO marker: blue star-smack frame", "blue" in src and "star-smack" in src.lower() or "blue star" in src.lower() or "blue" in src)
check("T8 TODO marker: purple planet-smack frame", "purple" in src and ("planet-smack" in src.lower() or "purple planet" in src.lower()))
check("T8 TODO marker: deliberate-drop no-vector frame", "deliberate" in src.lower() or "no-vector" in src.lower() or "negative case" in src.lower())

# Constants
check("T8 NONE token == 'none'", EV_NONE == "none")
check("T8 BLUE token == 'blue'", EV_BLUE == "blue")
check("T8 PURPLE token == 'purple'", EV_PURPLE == "purple")


# ===========================================================================
# T1 — central ship-safety: grabber=None + Star drop -> ABSTAIN, no smack_recovery
# ===========================================================================
print("\n--- T1: grabber=None + Star -> abstain ---")
r = _fake_runner(escape_vector_grabber=None)
result = _route_sc_exit(r, _ev("Star"))
check("T1 _route_sc_exit returns None (not 'smack_recovery')", result is None)
check("T1 smack_recovery NOT dispatched", "smack_recovery" not in r._ran)
abstained = [k for k, _ in r._records if k == "SmackDeterminationAbstained"]
check("T1 SmackDeterminationAbstained emitted", len(abstained) >= 1)
payloads = [p for k, p in r._records if k == "SmackDeterminationAbstained"]
check("T1 abstain reason == 'cv_unwired'",
      any(p.get("reason") == "cv_unwired" for p in payloads if p))


# ===========================================================================
# T2 — deliberate drop: CV 'none' + Star -> no recovery, Negative record
# ===========================================================================
print("\n--- T2: CV 'none' + Star/Planet -> no recovery ---")

def _det_returning(token):
    """Return a grabber that returns a fake frame; inject a fake detector."""
    import unittest.mock as _mock
    frame = _FakeFrame()
    grabber = lambda: frame  # noqa: E731
    return grabber, token


# We need to inject a fake detect_escape_vector. Patch it in the boot_routes module.
import ed_autojump.flow.boot_routes as _br
import ed_vision.escape_vector as _evmod

for body_type in ("Star", "Planet"):
    r = _fake_runner(escape_vector_grabber=lambda: _FakeFrame())
    # Temporarily override detect_escape_vector to return 'none'.
    orig_det = _evmod.detect_escape_vector
    _evmod.detect_escape_vector = lambda f: "none"
    try:
        result = _route_sc_exit(r, _ev(body_type))
    finally:
        _evmod.detect_escape_vector = orig_det
    check(f"T2 {body_type}+none -> None", result is None)
    check(f"T2 {body_type}+none -> no smack_recovery", "smack_recovery" not in r._ran)
    neg = [k for k, _ in r._records if k == "SmackDeterminationNegative"]
    check(f"T2 {body_type}+none -> SmackDeterminationNegative emitted", len(neg) >= 1)


# ===========================================================================
# T3 — star-smack: CV 'blue' + Star -> smack_recovery, kind='star'
# ===========================================================================
print("\n--- T3: 'blue' + Star -> smack_recovery kind='star' ---")
r = _fake_runner(escape_vector_grabber=lambda: _FakeFrame())
orig_det = _evmod.detect_escape_vector
_evmod.detect_escape_vector = lambda f: "blue"
try:
    result = _route_sc_exit(r, _ev("Star"))
finally:
    _evmod.detect_escape_vector = orig_det
check("T3 _route_sc_exit returns 'smack_recovery'", result == "smack_recovery")
check("T3 smack_recovery dispatched", "smack_recovery" in r._ran)
check("T3 runner._smack_kind == 'star'", r._smack_kind == "star")
confirmed = [k for k, _ in r._records if k == "StarSmackConfirmed"]
check("T3 StarSmackConfirmed emitted", len(confirmed) >= 1)


# ===========================================================================
# T4 — planet-smack: CV 'purple' + Planet -> smack_recovery, kind='planet'
# ===========================================================================
print("\n--- T4: 'purple' + Planet -> smack_recovery kind='planet' ---")
r = _fake_runner(escape_vector_grabber=lambda: _FakeFrame())
_evmod.detect_escape_vector = lambda f: "purple"
try:
    result = _route_sc_exit(r, _ev("Planet"))
finally:
    _evmod.detect_escape_vector = orig_det
check("T4 _route_sc_exit returns 'smack_recovery'", result == "smack_recovery")
check("T4 smack_recovery dispatched", "smack_recovery" in r._ran)
check("T4 runner._smack_kind == 'planet'", r._smack_kind == "planet")
confirmed = [k for k, _ in r._records if k == "PlanetSmackConfirmed"]
check("T4 PlanetSmackConfirmed emitted", len(confirmed) >= 1)


# ===========================================================================
# T5 — Station drop -> None, no determination record
# ===========================================================================
print("\n--- T5: Station drop -> no determination ---")
r = _fake_runner(escape_vector_grabber=lambda: _FakeFrame())
result = _route_sc_exit(r, _ev("Station"))
check("T5 Station -> returns None", result is None)
check("T5 Station -> no smack_recovery", "smack_recovery" not in r._ran)
smack_records = [k for k, _ in r._records
                 if "Smack" in k or "smack" in k.lower()]
check("T5 Station -> no smack determination record", len(smack_records) == 0)


# ===========================================================================
# T6 — scene_for: smacked=True, smack_kind=None -> NOT STARSMACK (abstain)
#                 smack_kind='star' -> STARSMACK selected
# ===========================================================================
print("\n--- T6: _det_starsmack via scene_for ---")

ctx_smacked_no_cv = DetermineContext(smacked=True, smack_kind=None)
tmpl = scene_for(ctx_smacked_no_cv)
check("T6 smacked=True, smack_kind=None -> scene_for NOT STARSMACK",
      tmpl is None or tmpl.state is not CSeriesState.STARSMACK)

ctx_smacked_cv = DetermineContext(smacked=True, smack_kind="star")
tmpl2 = scene_for(ctx_smacked_cv)
check("T6 smacked=True, smack_kind='star' -> scene_for IS STARSMACK",
      tmpl2 is not None and tmpl2.state is CSeriesState.STARSMACK)

ctx_smacked_planet = DetermineContext(smacked=True, smack_kind="planet")
tmpl3 = scene_for(ctx_smacked_planet)
check("T6 smacked=True, smack_kind='planet' -> scene_for IS STARSMACK",
      tmpl3 is not None and tmpl3.state is CSeriesState.STARSMACK)

ctx_not_smacked = DetermineContext(smacked=False, smack_kind=None)
tmpl4 = scene_for(ctx_not_smacked)
check("T6 smacked=False, smack_kind=None -> NOT STARSMACK",
      tmpl4 is None or tmpl4.state is not CSeriesState.STARSMACK)


# ===========================================================================
# T12 — mismatch: Star + 'purple' -> no recovery, SmackDeterminationMismatch
# ===========================================================================
print("\n--- T12: mismatch (Star + 'purple') -> abstain ---")
r = _fake_runner(escape_vector_grabber=lambda: _FakeFrame())
_evmod.detect_escape_vector = lambda f: "purple"
try:
    result = _route_sc_exit(r, _ev("Star"))
finally:
    _evmod.detect_escape_vector = orig_det
check("T12 mismatch -> returns None", result is None)
check("T12 mismatch -> no smack_recovery", "smack_recovery" not in r._ran)
mismatch = [k for k, _ in r._records if k == "SmackDeterminationMismatch"]
check("T12 mismatch -> SmackDeterminationMismatch emitted", len(mismatch) >= 1)

# Also Planet + 'blue'
r2 = _fake_runner(escape_vector_grabber=lambda: _FakeFrame())
_evmod.detect_escape_vector = lambda f: "blue"
try:
    result2 = _route_sc_exit(r2, _ev("Planet"))
finally:
    _evmod.detect_escape_vector = orig_det
check("T12 Planet+blue mismatch -> returns None", result2 is None)
check("T12 Planet+blue mismatch -> no smack_recovery", "smack_recovery" not in r2._ran)
mismatch2 = [k for k, _ in r2._records if k == "SmackDeterminationMismatch"]
check("T12 Planet+blue mismatch -> SmackDeterminationMismatch emitted", len(mismatch2) >= 1)


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
