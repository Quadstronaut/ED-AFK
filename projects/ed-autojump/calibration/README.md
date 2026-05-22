# In-game calibration

Tier-C requirements (FSS, DSS, docking align) read a per-install profile at
`calibration/profile.json` so HSV thresholds adapt to the user's specific
HDR / EDHM / resolution setup. Tier-A behaviour (req 1, 2, 3, 4, 7) does
not depend on this file.

## What needs in-game calibration

| Phase | Requirement | In-game step |
|---|---|---|
| 6 | EDHM detection + GraphicsConfigurationOverride | None — detection runs at startup. Override write needs `--consent` flag. |
| 7 | FSS keyboard sweep (req 5 Path A) | Open FSS in a familiar system. Confirm `ExplorationFSSEnter` from the ED-AFK preset enters FSS; `ExplorationFSSRadioTuningX_Decrease` sweeps the band. Run `ed-autojump fss-calibrate` (TODO) once. |
| 8 | FSS CV-assisted (req 5 Path B) | Run `ed-autojump calibrate --hud` and click the HUD-anchor pixels when prompted. |
| 9 | DSS 6-direction (req 6) | Map a single low-value rocky body manually with the bot in observe-only mode and let it record the per-class probe-pattern timings. |
| 10 | Docking (v2) | Approach a known station; let the Advanced Docking Computer handle the mailslot. |
| 11 | Headless launcher (v2) | Install `min-ed-launcher` and confirm it can autorun the game from the bot. |

## Why this exists

- Template matching at fixed RGB / HSV breaks across HDR, EDHM palettes,
  resolution scaling, and Windows DPI.
- Per-install calibration adapts to all of the above without code changes.

## Profile schema

See `src/ed_autojump/hud/calibration.py`. Fields:

- `hud_primary_hsv` / `hud_secondary_hsv` — captured at user-clicked anchor pixels.
- `background_value_max` — typical near-black HSV V for masking.
- `screen_w`, `screen_h` — capture resolution for ROI scaling.
- `hdr_active` — `IDXGIOutput6::GetDesc1().ColorSpace`-detected at startup.
- `rois_relative` — fractional (x, y, w, h) ROIs for each tier-C operation.
- `schema_version` — bumped if we change the file shape.

## How to validate without the game

Tier-C tests gated with `@pytest.mark.requires_game` are skipped by default
in CI. Run them explicitly when the game is open:

```pwsh
pytest -m requires_game
```
