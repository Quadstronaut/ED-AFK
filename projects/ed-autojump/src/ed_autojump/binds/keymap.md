# ED-AFK bot keymap

This is the **single source of truth** for the bot's binds. Edit the `Key`
column, then run:

```
python -m ed_autojump.binds_generate
```

That regenerates `ED-AFK.4.2.binds` next to this file. The generator
lints first — typo'd scancodes, missing actions, and key collisions all
fail closed. The `.binds` file should never be hand-edited; it is
overwritten on every regenerate.

Valid `Key` values are the `Key_*` names from
`src/ed_autojump/keys/scancodes.py` (e.g. `Key_J`, `Key_Numpad_5`,
`Key_Space`). Leave a `Key` blank to deliberately unbind an action (the
generator will write `<Primary Key="" Device="{NoDevice}" />`).

## Actions the bot presses

If the bot ever calls an action not in this table, lint refuses to
generate until you add it. If a key is bound to more than one action,
lint refuses to generate. Bot ≠ player: this preset is loaded only when
the bot is active. Fly with a different ED preset.

| Action                      | Key | Used by                              |
| ----------------------------| ----| -------------------------------------|
| HyperSuperCombination       |     | engage_jump                          |
| Supercruise                 |     | engage_supercruise, smack_recovery   |
| SelectTarget                |     | target_ahead                         |
| TargetNextRouteSystem       |     | target_next_route                    |
| SetSpeedZero                |     | set_throttle pct=0                   |
| SetSpeed25                  |     | set_throttle pct=25                  |
| SetSpeed50                  |     | set_throttle pct=50 (smack_recovery) |
| SetSpeed75                  |     | set_throttle pct=75                  |
| SetSpeed100                 |     | set_throttle pct=100 (jump)          |
| PitchUpButton               |     | orient_compass, pitch_compass        |
| PitchDownButton             |     | orient_compass                       |
| YawLeftButton               |     | orient_compass                       |
| YawRightButton              |     | orient_compass                       |
| ExplorationFSSDiscoveryScan |     | honk parallel track                  |
| FocusLeftPanel              |     | nav_panel_target, sc_assist_orbit    |
| UI_Select                   |     | nav_panel_target, sc_assist_orbit    |
| UI_Right                    |     | sc_assist_orbit                      |
| DeployHeatSink              |     | heat_guard                           |
