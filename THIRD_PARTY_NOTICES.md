# Third-party notices

This repository incorporates patterns, constants, and schema references from
the following projects. All borrowed material is MIT or BSD-compatible.

| Source | License | Used for |
|---|---|---|
| [SumZer0-git/EDAPGui](https://github.com/SumZer0-git/EDAPGui) | MIT | DirectInput Set 1 scancode table (`src/ed_autojump/keys/scancodes.py`); `.binds` parser shape (`keys/binds.py`); Status.json poller pattern (`status/status.py`); NavRoute parser pattern (`status/navroute.py`) |
| [EDCD/coriolis-data](https://github.com/EDCD/coriolis-data) | MIT | FSD per-class+rating constants — `modules/standard/frame_shift_drive.json` transcribed into `src/ed_autojump/data/fsd_modules.json` |
| [EDCD/EDDN](https://github.com/EDCD/EDDN) | BSD-2-Clause | Canonical schema field reference for `fssdiscoveryscan-v1.0`, `fssallbodiesfound-v1.0`, `fssbodysignals-v1.0`, `journal-v1.0`, `navroute-v1.0` — used when defining pydantic event models and the EDDN publisher (`eddn/`) |
| [EDCD/FDevIDs](https://github.com/EDCD/FDevIDs) | MIT | Reference for module Item IDs (`int_fuelscoop_*`, `int_detailedsurfacescanner_tiny`, etc.) |

## Frontier-supplied data

The `.binds` XML schema, journal event field names, and Status.json field
shapes are documented in the publicly-released Frontier Player Journal
Manual v32 and the published `.binds` schemas. No Frontier files are
modified or redistributed.

Real journal samples used as test fixtures
(`projects/ed-autojump/tests/fixtures/journals/*.log`) are anonymized
excerpts of the developer's own journal recordings — system names and
commander identifiers are replaced.
