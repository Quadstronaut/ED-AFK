"""Synthetic-held-PrimaryFire pip-reset probe — does the honk's held key break the reset?

THE DELTA UNDER TEST
  The plain pip_probe.py (no held key) resets pips to 2/2/2 EVERY time — operator-confirmed.
  The LIVE bot fails to reset pips EVERY time. The single deterministic difference: in the
  live run the honk track holds PrimaryFire DOWN (synthetic scancode_keyDown, no keyUp) for
  ~5-30s WHILE the pip-reset taps fire. This probe replicates that one delta and nothing else,
  on the SAME sender code path the bot uses (DirectInputSender.key_down / .press / .key_up).

WHAT IT DOES (identical send path to the bot)
  1. focus ED.
  2. CONTROL leg: with NOTHING held, press ResetPowerDistribution x3 @0.8s — proves the sender
     + scancode are live in THIS process (re-confirms plain pip_probe in-process).
  3. Re-set pips to 4-ENGINES by hand (you, on the keyboard) during the countdown.
  4. TEST leg: sender.key_down("PrimaryFire")  <-- the synthetic hold, exactly like honk's
     step_hold_until_event (steps.py:427).  THEN press ResetPowerDistribution x3 @0.8s
     (exactly step_reset_power_distribution: 0.8s pre-settle + 0.8s between, presses=3).
     THEN sender.key_up("PrimaryFire").

  IMPORTANT: this single-threaded script holds PrimaryFire from the MAIN thread, whereas the
  bot holds it from a SEPARATE daemon thread sharing the one sender. That thread split does NOT
  change the OS-level input state (SendInput is process-global; Windows tracks one key-state
  table per thread-input-queue, and pydirectinput-rgx targets the foreground window's queue
  regardless of which Python thread calls it). So a held key looks identical to ED either way.
  If the bug were a pydirectinput-rgx PER-THREAD held-key bookkeeping issue, this probe would
  MISS it — see the note in the result table.

READ THE RESULT
  CONTROL pips reset 2/2/2, TEST pips DO reset 2/2/2
      -> the synthetic held key is NOT the cause. Look elsewhere (the real live delta is then
         the thread split OR something non-honk: re-run pip_probe_honk_threaded.py — ask the
         council). DO NOT ship a honk-release fix; it would not help.
  CONTROL pips reset 2/2/2, TEST pips STAY full-engines (do NOT reset)
      -> CONFIRMED: a synthetic-held PrimaryFire deterministically suppresses the concurrent
         extended Down tap (fire-held + Down read by ED as a fire-group/combined input, or
         rgx mangles the extended sequence while another key is held). The honk hold IS the
         cause. Fix: release PrimaryFire immediately before the pip reset (and let honk
         re-acquire), OR move the pip reset outside the honk-hold window.
  CONTROL ALSO fails to reset
      -> the sender/scancode is not live in this process at all (focus lost, wrong binds);
         the run is void — re-run, click ED first. Tells you nothing about the held key.

SAFE: sends NO throttle and NO movement. PrimaryFire in ANALYSIS mode is the discovery
scanner (a honk), not weapons — set the ship to ANALYSIS mode before running, parked, as the
bot does (ensure_analysis_mode is honk step 0). The key is ALWAYS released in a finally.

Run from projects/ed-autojump:  python pip_probe_honk.py
Then ALT-TAB to Elite and watch the pips across the CONTROL leg and the TEST leg.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from ed_autojump.keys import DirectInputSender, parse_binds

BINDS = Path(__file__).parent / "src" / "ed_autojump" / "binds" / "ED-AFK.4.2.binds"
_SETTLE_S = 0.8   # exactly step_reset_power_distribution's spacer (steps.py:81)


def _reset_pips_x3(sender) -> None:
    """Replicate step_reset_power_distribution EXACTLY: 0.8 pre-settle, then
    3 presses each preceded by 0.8s (i>0)."""
    time.sleep(_SETTLE_S)
    for i in range(3):
        if i > 0:
            time.sleep(_SETTLE_S)
        sender.press("ResetPowerDistribution")


def _countdown(msg: str, n: int = 6) -> None:
    print(msg)
    for k in range(n, 0, -1):
        print(f"    {k}s ...")
        time.sleep(1)


def main() -> None:
    sender = DirectInputSender(parse_binds(BINDS))
    try:
        from ed_autojump.launcher.focus import focus_ed_window
        focus_ed_window()
    except Exception as exc:
        print(f"(auto-focus unavailable: {exc} — alt-tab to ED yourself)")

    print(">>> Put the ship in ANALYSIS mode, parked. Set pips to 4-ENGINES (hold Up).")
    _countdown("\n[CONTROL] no key held — ResetPowerDistribution x3 should reset pips to 2/2/2")
    _reset_pips_x3(sender)
    print("    CONTROL done. Pips should now read 2/2/2 (proves sender is live in-process).")

    _countdown("\n>>> Set pips BACK to 4-ENGINES (hold Up). Then the TEST leg holds PrimaryFire.")
    print("[TEST] sender.key_down(PrimaryFire)  <-- synthetic hold, like honk")
    sender.key_down("PrimaryFire")
    try:
        print("[TEST] ...holding PrimaryFire, now ResetPowerDistribution x3 @0.8s")
        _reset_pips_x3(sender)
    finally:
        sender.key_up("PrimaryFire")
        print("[TEST] PrimaryFire released.")

    print("\nDONE. Report BOTH: did CONTROL reset 2/2/2?  did TEST reset 2/2/2?")
    print("  CONTROL yes + TEST no  = held key CONFIRMED as the cause.")
    print("  CONTROL yes + TEST yes = held key is NOT the cause (it's the thread split or non-honk).")
    print("  CONTROL no             = void run, sender not live; re-run with ED focused.")


if __name__ == "__main__":
    main()
