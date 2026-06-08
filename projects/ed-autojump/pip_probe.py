"""Extended-key delivery probe — does the bot's synthetic ARROW key register in ED?

A/B test in one run:
  CONTROL: SetSpeed100 (Key_V, NON-extended) -> throttle should jump to 100%.
  TEST:    ResetPowerDistribution (Key_DownArrow, EXTENDED 0xE0 0x50) -> pips should snap to 2/2/2.

Read the result:
  - throttle -> 100% AND pips -> 2/2/2  : bot delivers extended keys; pip bug is elsewhere.
  - throttle -> 100% but pips UNCHANGED : CONFIRMED — non-extended works, EXTENDED (arrow) keys do not.
  - neither moves                       : ED was not focused / sender not live (re-run, click ED first).

Run from projects/ed-autojump:  python pip_probe.py
Then ALT-TAB to Elite within 6 seconds and watch the throttle + pips.
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

    print(">>> SET YOUR PIPS to 4-ENGINES (hold Up arrow) and watch throttle+pips.")
    for n in range(6, 0, -1):
        print(f"    firing in {n}s ...")
        time.sleep(1)

    print("\n[CONTROL] SetSpeed100  (Key_V, non-extended) -> throttle should hit 100%")
    sender.press("SetSpeed100")
    time.sleep(2)

    print("[TEST]    ResetPowerDistribution x3  (Key_DownArrow, EXTENDED) -> pips should reset to 2/2/2")
    for _ in range(3):
        sender.press("ResetPowerDistribution")
        time.sleep(0.8)

    print("\nDONE. Report: did throttle hit 100%?  did pips reset to 2/2/2?")


if __name__ == "__main__":
    main()
