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

The **Default** column shows ED's stock binding from
`ControlSchemes/ClassicKeyboardOnly.binds` (the pure-keyboard scheme — closest
match for a no-mouse bot). `—` = intentionally unbound by Frontier in that
scheme. Use it as a reference, not a constraint.

| Action                      | Key            | Used by                                  | Default        |
| ----------------------------| ---------------| -----------------------------------------| ---------------|
| HyperSuperCombination       | Key_J          | engage_jump                              | Key_J          |
| Supercruise                 | Key_K          | engage_supercruise, smack_recovery       | —              |
| SelectTarget                | Key_T          | target_ahead                             | Key_T          |
| TargetNextRouteSystem       | Key_H          | target_next_route                        | —              |
| SetSpeedZero                | Key_X          | set_throttle pct=0                       | —              |
| SetSpeed25                  | Key_C          | set_throttle pct=25                      | —              |
| SetSpeed50                  | Key_V          | set_throttle pct=50 (smack_recovery)     | —              |
| SetSpeed75                  | Key_B          | set_throttle pct=75                      | —              |
| SetSpeed100                 | Key_N          | set_throttle pct=100 (jump)              | —              |
| PitchUpButton               | Key_S          | orient_compass, pitch_compass            | Key_X          |
| PitchDownButton             | Key_W          | orient_compass                           | Key_S          |
| YawLeftButton               | Key_A          | orient_compass                           | —              |
| YawRightButton              | Key_D          | orient_compass                           | —              |
| ExplorationFSSDiscoveryScan | Key_Equals     | honk parallel track                      | Mouse_3        |
| FocusLeftPanel              | Key_1          | nav_panel_target, sc_assist_orbit        | Key_1          |
| FocusCommsPanel             | Key_2          | manual grab — comms panel                | —              |
| FocusRadarPanel             | Key_3          | manual grab — role/crew panel (panel 3)  | —              |
| FocusRightPanel             | Key_4          | manual grab — systems/ship panel         | —              |
| UI_Select                   | Key_Enter      | nav_panel_target, sc_assist_orbit        | Key_Enter      |
| UI_Left                     | Key_LeftArrow  | manual grab                              | Key_LeftArrow  |
| UI_Right                    | Key_RightArrow | sc_assist_orbit                          | Key_RightArrow |
| DeployHeatSink              | Key_Minus      | heat_guard                               | Key_E          |
| ShipSpotLightToggle         | Key_L          | manual grab — ship lights                | —              |
| NightVisionToggle           | Key_G          | manual grab — moved off N/SetSpeed100    | —              |