"""SET FILTERS screen management for step_explore — STUB-3 isolation.

All operator-blocked reads for the NAVIGATION filter sub-screen (GuiFocus
int + checkbox CV) live HERE so the rest of the exploration logic is
insulated from unverified UI constants.  Fill the TODOs below and nothing
else needs to change.

One-time permanent contract (PIN-F/PIN-G/Q1/Q2): establish_filters runs
ONCE, gated behind filters_latched()/mark_filters_latched().  The latch is
disk/journal side-state, NOT a StepContext field (PIN-E).  Subsequent systems
skip S0 entirely.

DAG: imports only ed_core; never imports a sibling domain.
"""

from __future__ import annotations

import pathlib

# ---------------------------------------------------------------------------
# Desired filter polarity (Q1 operator spec)
# Rows top-to-bottom on the SET FILTERS screen:
#   Stars, Asteroid Clusters, Planets and Moons, Landfall Planets and Moons,
#   Settlements, Stations, Carriers, Points of Interest, Signal Sources,
#   Systems, BACK
# ---------------------------------------------------------------------------
DESIRED_FILTERS: dict[str, bool] = {
    "Stars": True,
    "Asteroid Clusters": False,
    "Planets and Moons": True,
    "Landfall Planets and Moons": True,
    "Settlements": False,
    "Stations": True,
    "Carriers": False,
    "Points of Interest": True,
    "Signal Sources": False,
    "Systems": True,
}

# ---------------------------------------------------------------------------
# Disk latch — one-time permanent-set sentinel.
# File presence == latched; no content needed.
# Lives next to this module in the ed_explore package data directory.
# NOT a StepContext field (PIN-E).
# ---------------------------------------------------------------------------
_LATCH_PATH = pathlib.Path(__file__).parent / "_explore_filters_latched.flag"


def filters_latched() -> bool:
    """True once mark_filters_latched() has been called (persists across runs)."""
    return _LATCH_PATH.exists()


def mark_filters_latched() -> None:
    """Permanently record that the desired filter polarity has been confirmed."""
    _LATCH_PATH.touch(exist_ok=True)


# ---------------------------------------------------------------------------
# STUB-3  filter_screen_focused — GuiFocus gate (operator-blocked)
# ---------------------------------------------------------------------------

# TODO(operator): what is the GuiFocus int on the SET FILTERS sub-screen?
# Verified map: 0=cockpit, 2=NAV panel, 5=station services, 6=galaxy map,
# 7=system map, 9=FSS.  The filter sub-screen value is UNVERIFIED (baseline
# notes 1-vs-2 as candidate but not confirmed; build fix 3).
# Set this to the correct integer once confirmed in-game and the stub goes live.
FILTER_SCREEN_GUI_FOCUS: int | None = None


def filter_screen_focused(ctx) -> bool:
    """Return True when Status.gui_focus matches the SET FILTERS sub-screen.

    STUB-3 — fails closed (returns False) while FILTER_SCREEN_GUI_FOCUS is
    None, preventing any toggle of PERMANENT nav-filter state on an unconfirmed
    screen (PIN-G / build fix 3).  Never raises.

    TODO(operator): pin the GuiFocus int for the SET FILTERS sub-screen and
    set FILTER_SCREEN_GUI_FOCUS above to that value.
    """
    if FILTER_SCREEN_GUI_FOCUS is None:
        return False  # fail-closed in the interim
    try:
        st = ctx.status_supplier()
        return st is not None and getattr(st, "gui_focus", None) == FILTER_SCREEN_GUI_FOCUS
    except Exception:
        return False  # never raises


# ---------------------------------------------------------------------------
# read_checkbox_states — CV calibration-pending
# ---------------------------------------------------------------------------

def read_checkbox_states(ctx) -> dict | None:
    """Read the current checkbox polarity for each DESIRED_FILTERS row.

    Returns a {row_name: bool} dict when the CV reader is calibrated, or None
    while calibration is pending.  Never raises.

    CALIBRATION-PENDING — returns None unconditionally until a calibration
    frame (navpanel_filters_screen.png with known states) is provided and the
    per-row accuracy gate (PIN-G) passes.

    TODO(operator): provide a navpanel_filters_screen.png fixture with KNOWN
    checkbox states; implement per-row CV accuracy measurement; set a
    GROUND-TRUTH threshold (not a frame-confidence score); wire the reader
    here only once the threshold is met.
    """
    return None  # CALIBRATION-PENDING


# ---------------------------------------------------------------------------
# establish_filters — one-time read-before-write + read-after-write confirm
# ---------------------------------------------------------------------------

def establish_filters(ctx) -> bool:
    """Confirm the nav filter polarity matches DESIRED_FILTERS; toggle as needed.

    Returns True when all desired rows are confirmed at the correct polarity.
    Returns False (no-op) while any stub/calibration dependency is pending.
    Never raises.

    Full contract (PIN-F/PIN-G):
      1. Gate on filter_screen_focused(ctx) — ensures we are on the right screen.
      2. read_checkbox_states(ctx) — per-row READ-BEFORE-WRITE; must pass the
         ground-truth accuracy threshold (calibration gate, PIN-G).
      3. Toggle only rows that differ (UI_Select per row).
      4. Re-read to confirm (READ-AFTER-WRITE).
      5. Only when ALL rows are at the desired polarity: return True.

    INERT while STUB-3 and read_checkbox_states are unfilled (both return
    fail-closed / None), so step_explore's S0 cleanly fails closed to TRAVERSAL.
    """
    # STUB-3: filter_screen_focused is False while FILTER_SCREEN_GUI_FOCUS is None.
    if not filter_screen_focused(ctx):
        return False
    # CALIBRATION-PENDING: read_checkbox_states returns None.
    states = read_checkbox_states(ctx)
    if states is None:
        return False
    # TODO(operator): implement the read-before-write + toggle + read-after-write
    # confirm loop once both stubs above are filled.  For now, return False so
    # S0 fails closed and no PERMANENT nav-filter state is toggled on unproven data.
    return False
