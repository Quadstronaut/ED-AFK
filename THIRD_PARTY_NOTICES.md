# Third-party notices

This repository incorporates patterns, constants, and schema references from
the following projects. All borrowed *code/patterns* are MIT or BSD-compatible.
**Exception:** the bundled nav-compass detection model weights are AGPL-3.0 —
see ["Bundled ML model"](#bundled-ml-model-agpl-30) below before redistributing.

| Source | License | Used for |
|---|---|---|
| [SumZer0-git/EDAPGui](https://github.com/SumZer0-git/EDAPGui) | MIT | DirectInput Set 1 scancode table (`src/ed_autojump/keys/scancodes.py`); `.binds` parser shape (`keys/binds.py`); Status.json poller pattern (`status/status.py`); NavRoute parser pattern (`status/navroute.py`); nav-compass alignment approach (`src/ed_autojump/vision/`, `executor/align.py`) |
| [SumZer0-git/EDAPGui](https://github.com/SumZer0-git/EDAPGui) — `Yolo26/compass-model` weights | **AGPL-3.0** (Ultralytics) | Vendored as `src/ed_autojump/vision/model/compass.onnx` + `compass.pt` — the YOLO26n model that detects the compass + navpoint for ship orientation |
| [EDCD/coriolis-data](https://github.com/EDCD/coriolis-data) | MIT | FSD per-class+rating constants — `modules/standard/frame_shift_drive.json` transcribed into `src/ed_autojump/data/fsd_modules.json` |
| [EDCD/EDDN](https://github.com/EDCD/EDDN) | BSD-2-Clause | Canonical schema field reference for `fssdiscoveryscan-v1.0`, `fssallbodiesfound-v1.0`, `fssbodysignals-v1.0`, `journal-v1.0`, `navroute-v1.0` — used when defining pydantic event models and the EDDN publisher (`eddn/`) |
| [EDCD/FDevIDs](https://github.com/EDCD/FDevIDs) | MIT | Reference for module Item IDs (`int_fuelscoop_*`, `int_detailedsurfacescanner_tiny`, etc.) |

## Bundled ML model (AGPL-3.0)

The nav-compass alignment feature ships a small object-detection model
(`src/ed_autojump/vision/model/compass.{onnx,pt}`, YOLO26n, ~10 MB). It was
trained with [Ultralytics](https://github.com/ultralytics/ultralytics) and
its embedded metadata declares **AGPL-3.0** (`license: AGPL-3.0 License
(https://ultralytics.com/license)`). The weights are reused from EDAPGui's
`Yolo26/compass-model`.

**Decision (2026-05-24):** the maintainer chose to ship the bundled weights and
license the **entire distribution as AGPL-3.0-or-later** (see
`projects/ed-autojump/LICENSE`). The options below are retained for forkers who
want a different trade-off.

**Implication for redistribution:** AGPL-3.0 is a strong copyleft license.
Bundling these weights inside this otherwise-MIT package means the *combined
distribution* that includes the model carries AGPL obligations (notably:
network/use of the covered work may require offering corresponding source).
This does **not** affect the MIT licensing of ed-autojump's own code, but it
does affect how you may ship a build that includes the model.

Options if you want to keep this package cleanly MIT-distributable:
1. **Don't vendor the weights.** Remove `vision/model/*.{onnx,pt}` and have
   users supply their own model file, or download it at first run with a
   clear AGPL notice. The OpenCV fallback (`backend = "opencv"`) needs no
   model and is unaffected.
2. **Train a replacement** under a license you control and drop it in.
3. **Keep the bundle and license the whole distribution AGPL-3.0.**

The default light path (`backend = "yolo-onnx"`) and opt-in heavy path
(`backend = "ultralytics"`) both use these AGPL weights; the colour-free
OpenCV fallback does not.

## Frontier-supplied data

The `.binds` XML schema, journal event field names, and Status.json field
shapes are documented in the publicly-released Frontier Player Journal
Manual v32 and the published `.binds` schemas. No Frontier files are
modified or redistributed.

Real journal samples used as test fixtures
(`projects/ed-autojump/tests/fixtures/journals/*.log`) are anonymized
excerpts of the developer's own journal recordings — system names and
commander identifiers are replaced.
