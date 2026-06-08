"""Panel-context pip-reset probe — does the bot's synthetic reset work with the
left nav panel OPEN? (The live arrival flow opens that panel right before reset.)

No throttle this time — safe to run parked.

Sequence:
  1. you set pips to 4-ENGINES manually, then ALT-TAB to ED.
  2. probe OPENS the left nav panel (FocusLeftPanel).
  3. probe fires ResetPowerDistribution 3x at 0.8s  <-- the exact live step.
  4. probe CLOSES the left nav panel.

Read the result:
  - pips reset to 2/2/2 WHILE THE PANEL WAS OPEN : panel is NOT the cause; chase ED-foreground/focus.
  - pips stayed 4-engines (only the panel opened/closed) : CONFIRMED — synthetic reset is
    swallowed while a panel has focus. Fix = ensure cockpit focus (close panels) BEFORE the reset.

Run from projects/ed-autojump:  python pip_probe_panel.py   (then alt-tab to ED within 6s)
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from ed_autojump.keys import DirectInputSender, parse_binds

BINDS = Path(__file__).parent / "src" / "ed_autojump" / "binds" / "ED-AFK.4.2.binds"


def main() -> None:
    sender = DirectInputSender(parse_binds(BINDS))
    try:
        from ed_autojump.launcher.focus import focus_ed_window
        focus_ed_window()
    except Exception as exc:
        print(f"(auto-focus unavailable: {exc} — alt-tab to ED yourself)")

    print(">>> SET PIPS to 4-ENGINES, then ALT-TAB to Elite. Watch the pips + the panel.")
    for n in range(6, 0, -1):
        print(f"    starting in {n}s ...")
        time.sleep(1)

    print("\n[1] FocusLeftPanel -> opening the left nav panel")
    sender.press("FocusLeftPanel")
    time.sleep(1.5)

    print("[2] ResetPowerDistribution x3 @0.8s  (panel is OPEN — this mirrors live arrival)")
    for _ in range(3):
        sender.press("ResetPowerDistribution")
        time.sleep(0.8)

    print("    (did pips reset to 2/2/2 while the panel was open? remember the answer)")
    time.sleep(1.0)

    print("[3] FocusLeftPanel -> closing the panel")
    sender.press("FocusLeftPanel")

    print("\nDONE. Report: did the pips reset to 2/2/2 WHILE the panel was open?")


if __name__ == "__main__":
    main()
