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

from ..keys.sender import Sender


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
