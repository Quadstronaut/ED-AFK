# Pips — Power Distribution

The ship's power distributor ("pips") is driven by the four arrow keys. Each tap shifts
one pip toward that target; **Down** balances them back to an even split.

| Direction | Target | What it feeds | ED bind | Key | Scancode |
|-----------|--------|---------------|---------|-----|----------|
| **Left**  | SYS | systems — shields, utilities | `IncreaseSystemsPower`   | Key_LeftArrow  | `0xE0 0x4B` |
| **Up**    | ENG | engines — speed, boost       | `IncreaseEnginesPower`   | Key_UpArrow    | `0xE0 0x48` |
| **Right** | WEP | weapons                      | `IncreaseWeaponsPower`   | Key_RightArrow | `0xE0 0x4D` |
| **Down**  | —   | reset to equal / balanced distribution | `ResetPowerDistribution` | Key_DownArrow | `0xE0 0x50` |

## Status (2026-06-18)
- All four binds exist in the preset `projects/ed-autojump/src/ed_autojump/binds/ED-AFK.4.2.binds` (lines 413–428).
- **Zero callers in code.** Pip management (`pips_engines` / `reset_power_distribution`) was ripped from the bot on 2026-06-08; the binds are currently dead (not in `REQUIRED_ACTIONS`, no `register_step`).
- This file is the placement/wiring reference for re-adding pips wherever you want them in the flow.
