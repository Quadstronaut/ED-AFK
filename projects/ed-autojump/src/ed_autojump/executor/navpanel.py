"""
Engage Supercruise Assist via the left nav panel (blind keystroke macro).

WHY THIS IS A NAV-PANEL MACRO AND NOT A KEYBIND
-----------------------------------------------
Supercruise Assist has NO dedicated keybind in Elite Dangerous. You cannot
press one key to turn it on. The only way to toggle it is through the UI:
open the left ("navigation") panel, select a destination body to open its
*detail pane*, and there — to the right of the body's name — sits the
"LOCK AND SUPERCRUISE" control. Activating that control engages assist and
flies you to the body automatically. When assist is already running the
same control reads "DEACTIVATE SUPERCRUISE ASSIST" instead, but its screen
position (one step right of the selected row) is unchanged.

WHY THIS IS BLIND (no vision / no CV)
-------------------------------------
On arrival from a hyperspace jump, the ship drops in right next to the
system's primary star. The nav panel lists bodies by distance, closest
first — so the arrival star is the TOP row and, because it's the nearest
body, it is SELECTED BY DEFAULT the instant the panel opens. That gives us
a fixed, known starting cursor position with no need to read the screen:

    FocusLeftPanel  -> panel opens, top row (the star) already highlighted
    UI_Select       -> open that row's detail pane
    UI_Right        -> move cursor onto the Supercruise Assist control
    UI_Select       -> activate it ("hit space again" — engages assist)
    FocusLeftPanel  -> close the panel

The UI animates between each step, so we sleep a short `settle_s` between
presses to let the highlight/pane catch up before the next keystroke lands.
All timing is injected (`sleeper`) so tests run instantly.

NO DEACTIVATE HELPER HERE
-------------------------
In the live flight loop, assist is never "deactivated" through this pane.
The next jump is set up with `TargetNextRouteSystem`, which both cancels
the active Supercruise Assist AND retargets the next route star in one
press — so the orchestrator gets deactivation for free. A standalone
deactivate macro would just re-walk this same pane to hit the
"DEACTIVATE SUPERCRUISE ASSIST" label, which nothing in the flow needs.
If that ever changes, the macro is symmetric: the identical key sequence
toggles assist off, because the control sits in the same place either way.
"""

from __future__ import annotations

import time
from typing import Callable

from ed_core.keys.sender import Sender


# Default settle between UI keystrokes. The nav panel animates its
# highlight and slides the detail pane open; ~0.4s is comfortably past the
# animation so the next press targets the right element. Injected/overridable.
DEFAULT_SETTLE_S = 0.4


def engage_supercruise_assist(
    sender: Sender,
    *,
    sleeper: Callable[[float], None] = time.sleep,
    settle_s: float = DEFAULT_SETTLE_S,
    panel_focus_action: str = "FocusLeftPanel",
) -> None:
    """Run the blind nav-panel macro that turns on Supercruise Assist.

    Sequence (see module docstring for the full WHY):

        panel_focus_action  -> open the left nav panel; the arrival star is
                               the top row and is selected by default
        UI_Select           -> open the star's detail pane
        UI_Right            -> move onto the Supercruise Assist control
        UI_Select           -> activate it (engage assist)
        panel_focus_action  -> close the nav panel

    A `settle_s` sleep is injected after every press so the UI animation
    finishes before the next keystroke. `sender` and `sleeper` are injected
    (the codebase does this everywhere) so tests neither send real keys nor
    actually sleep.

    `panel_focus_action` defaults to "FocusLeftPanel" — the ED action the
    bundled preset maps to Key_1, which is the key the user presses to open
    the left nav panel.

    Raises KeyError (via the sender) if any action is unbound; the binds
    preset binds all of them.
    """
    # Open the nav panel. Star is top row + default-selected on arrival.
    sender.press(panel_focus_action)
    sleeper(settle_s)

    # Open the star's detail pane.
    sender.press("UI_Select")
    sleeper(settle_s)

    # Move the cursor right onto the "LOCK AND SUPERCRUISE" control.
    sender.press("UI_Right")
    sleeper(settle_s)

    # Activate the control — this engages Supercruise Assist.
    sender.press("UI_Select")
    sleeper(settle_s)

    # Close the nav panel.
    sender.press(panel_focus_action)
    sleeper(settle_s)


def engage_supercruise_assist_row(
    sender: Sender,
    *,
    sleeper: Callable[[float], None] = time.sleep,
    settle_s: float = DEFAULT_SETTLE_S,
    row: int = 0,
    pin_to_top: bool = True,
    pin_hold_s: float = 4.0,
    panel_focus_action: str = "FocusLeftPanel",
) -> None:
    """engage_supercruise_assist GENERALIZED to an arbitrary nav-panel row k.

    engage_supercruise_assist (above) only ever targets the DEFAULT-selected
    top row (the arrival star on a hyperspace drop). The body-touring
    subsystem (body_tour) needs to lock + SC-assist toward bodies BELOW the
    star, so this variant prepends the same pin+walk prelude
    `_target_pin_and_walk` that target_via_navpanel / request_docking already
    use, then runs the identical SC-assist tail:

        panel_focus_action  -> open the left nav panel
        [pin to top + walk]  -> _target_pin_and_walk: pin the (persistent,
                               M4) cursor to row 0, then UI_Down x`row`
        UI_Select           -> open that row's detail pane (lands on Lock
                               Destination)
        UI_Right            -> move onto the LOCK AND SUPERCRUISE control
        UI_Select           -> activate it -> lock + engage assist (M3/D3)
        panel_focus_action  -> close the nav panel

    `row=0, pin_to_top=True` reproduces engage_supercruise_assist's star path
    plus the M4 pin (the held up-key saturates at row 0, so the pin is safe
    on the top row too). A SINGLE panel open does BOTH the lock and the
    engage (D3) — never split across two opens.

    NO new binds: FocusLeftPanel / UI_Down / UI_Up / UI_Select / UI_Right are
    all already in REQUIRED_ACTIONS. Raises KeyError (via the sender) on any
    unbound key; the caller (step_body_tour) catches it.
    """
    sender.press(panel_focus_action)
    sleeper(settle_s)
    _target_pin_and_walk(sender, sleeper, settle_s, row, pin_to_top, pin_hold_s)
    sender.press("UI_Select")
    sleeper(settle_s)
    sender.press("UI_Right")
    sleeper(settle_s)
    sender.press("UI_Select")
    sleeper(settle_s)
    sender.press(panel_focus_action)
    sleeper(settle_s)


def target_via_navpanel(
    sender: Sender,
    *,
    sleeper: Callable[[float], None] = time.sleep,
    settle_s: float = DEFAULT_SETTLE_S,
    panel_focus_action: str = "FocusLeftPanel",
    rows_down: int = 0,
    pin_to_top: bool = False,
    pin_hold_s: float = 4.0,
) -> None:
    """Run the blind nav-panel macro that TARGETS the top row (the closest
    body — the arrival star on a smack drop) without engaging Supercruise
    Assist.

    Sequence:

        panel_focus_action  -> open the left nav panel; top row (the star)
                               is selected by default (closest body)
        UI_Select           -> open the row's detail pane; cursor lands on
                               "Lock Destination" (the FIRST control in
                               the target submenu)
        UI_Select           -> activate Lock Destination -> target locked
        panel_focus_action  -> close the panel

    Same first-row mechanic as `engage_supercruise_assist`: closest body is
    row 0, auto-highlighted on panel-open. Differs by ONE press: no
    `UI_Right` step past "Lock Destination", so the second `UI_Select`
    activates the target lock instead of LOCK & SUPERCRUISE.

    Used in smack recovery to give pitch_compass a compass dot to home on
    (the arrival star) when SelectTarget can't be relied on — after a smack
    drop the ship may not have the star ahead of the reticle, so a regular
    SelectTarget would either lock nothing or lock the wrong body.

    `rows_down` (ADDED 2026-06-07, council must-fix): "row 0 = the arrival
    star" is ONLY true seconds after a hyperspace drop in an unpopulated
    system. In a populated system the panel lists the NAV BEACON (and
    stations) first — the 10:30Z incident locked the beacon and the orbit
    no-oped. The caller verifies the lock identity via Status.Destination
    and retries with rows_down+1 to scroll past non-star rows.

    `pin_to_top` (2026-06-07, OPERATOR-TESTED mechanics — Col 285 OE-N
    b8-3): the panel CURSOR PERSISTS across panel closes and across jumps
    (it opened at ~row 10 one system after the first live refuel and the
    rows_down walk scrolled AWAY from the star). The tested facts: a HELD
    up-key stops and sticks at the top row, but TAPPING at the top WRAPS
    to the bottom — so the pin is the operator's exact sequence: tap DOWN
    once (off any top-edge state), then HOLD up for `pin_hold_s`. The hold
    saturates at row 0, so over-holding is safe — duration costs only
    time, never correctness. NEVER convert this to a tap burst.

    Raises KeyError (via the sender) if any action is unbound; the bundled
    preset binds all of them.
    """
    sender.press(panel_focus_action)
    sleeper(settle_s)
    _target_pin_and_walk(sender, sleeper, settle_s, rows_down,
                         pin_to_top, pin_hold_s)
    sender.press("UI_Select")
    sleeper(settle_s)
    sender.press("UI_Select")
    sleeper(settle_s)
    sender.press(panel_focus_action)
    sleeper(settle_s)


def _target_pin_and_walk(sender, sleeper, settle_s, rows_down,
                         pin_to_top, pin_hold_s):
    """Shared cursor-positioning prelude for the nav-panel macros: optionally
    pin the cursor to the top row, then walk `rows_down` rows down. Factored
    out of target_via_navpanel so request_docking reuses the SAME pin
    mechanics (the cursor-persists-across-jumps fact applies on the Contacts
    tab too)."""
    if pin_to_top:
        sender.press("UI_Down")          # one tap down, off the top edge
        sleeper(settle_s)
        sender.press("UI_Up", hold=pin_hold_s)   # HELD: saturates at row 0
        sleeper(settle_s)
    for _ in range(max(0, rows_down)):
        sender.press("UI_Down")
        sleeper(settle_s)


def request_docking(
    sender: Sender,
    *,
    sleeper: Callable[[float], None] = time.sleep,
    settle_s: float = DEFAULT_SETTLE_S,
    panel_focus_action: str = "FocusLeftPanel",
    tab_cycle_action: str = "CycleNextPanel",
    pin_to_top: bool = True,
    pin_hold_s: float = 4.0,
) -> None:
    """Blind nav-panel macro that REQUESTS DOCKING at the station.

    Operator-walked sequence (current binds — these are the LEFT PANEL keys):

        panel_focus_action  -> open the left ("navigation") panel
        tab_cycle_action x2 -> Navigation -> (Transactions) -> CONTACTS tab
                               (E x2: the station sits at position 0 on the
                               CONTACTS tab — on the NAVIGATION tab position 0
                               is the orbited BODY, not the station, which is
                               why we MUST be on Contacts)
        [pin to top]        -> the panel cursor persists across opens/jumps;
                               pin it to row 0 (tap down once off any top-edge
                               state, then HOLD up — saturates at the top,
                               WRAPS on taps) so the station is selected
        UI_Select           -> select the station -> opens its detail pane
                               (this ALSO targets the station)
        UI_Right            -> move to "Request Docking" (operator's physical
                               D key = UI_Right in the live binds — the detail
                               pane lays the docking control one step off the
                               selected row)
        UI_Select           -> send the docking request
        panel_focus_action  -> close the panel

    Mirrors engage_supercruise_assist / target_via_navpanel: a `settle_s`
    sleep after every press for the UI animation; `sender`/`sleeper` injected
    so tests neither key nor sleep. Selecting the contact OR the Request
    Docking control TARGETS the station, so Status.Destination reads the
    station after this runs.

    Raises KeyError (via the sender) if any action is unbound; the validator
    requires CycleNextPanel (E) — it was added to REQUIRED_ACTIONS for this.
    """
    sender.press(panel_focus_action)
    sleeper(settle_s)
    # Navigation -> Contacts (two tabs over). The station is position 0 on
    # the Contacts tab.
    sender.press(tab_cycle_action)
    sleeper(settle_s)
    sender.press(tab_cycle_action)
    sleeper(settle_s)
    # Pin the (persistent) cursor to the top row = the station at position 0.
    _target_pin_and_walk(sender, sleeper, settle_s, 0, pin_to_top, pin_hold_s)
    # Select the station -> open its detail pane (also targets it).
    sender.press("UI_Select")
    sleeper(settle_s)
    # Move onto the "Request Docking" control (operator's physical D = UI_Right).
    sender.press("UI_Right")
    sleeper(settle_s)
    # Send the request.
    sender.press("UI_Select")
    sleeper(settle_s)
    # Close the panel.
    sender.press(panel_focus_action)
    sleeper(settle_s)
