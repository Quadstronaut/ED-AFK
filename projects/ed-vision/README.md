# ed-vision

<p>
  <img alt="role" src="https://img.shields.io/badge/role-perception%20leaf-blue">
  <img alt="depends" src="https://img.shields.io/badge/imports-nothing%20in%20workspace-informational">
  <img alt="license" src="https://img.shields.io/badge/license-AGPL--3.0--or--later-A42E2B">
  <img alt="status" src="https://img.shields.io/badge/status-alpha-orange">
</p>

Pure **perception** for the ED-AFK bot — **frames in, measurements out**. The
bottom leaf of the workspace dependency DAG: it sends no keys, runs no maneuver,
and imports nothing else in the workspace. Engine is **Windows WinRT OCR** +
**OpenCV**, with an optional bundled compass ONNX/PyTorch model (AGPL weights)
and a colour-free OpenCV fallback that needs no weights.

Part of the ED-AFK workspace — see the [repo-root README](../../README.md) for
the full project and the licensing split.

## Readers

| Module | Reads |
|---|---|
| `capture.py` | Screen capture of the compass/CV regions + the live-run vision factory |
| `compass.py` · `reader.py` · `cyan_reader.py` | Nav-compass cyan target-dot orient (coarse align: centred + in-front) |
| `yolo.py` · `opencv_reader.py` · `ultralytics_reader.py` | Compass backends — light ONNX (default), colour-free OpenCV fallback, opt-in heavy Ultralytics |
| `widget_ring.py` | Mouse widget-ring **fine** align — the residual angle compass can't close |
| `hud_sc_indicators.py` | Center-HUD SC-assist prompts (ORBITING / ALIGN WITH TARGET / ALIGN WITH ESCAPE VECTOR / FSD-SCO-MALFUNCTIONED) **and** the **CONNECTION ERROR** modal detector |
| `ocr_winrt.py` | WinRT OCR engine for the nav-panel read layer |
| `navpanel_reader.py` · `navpanel_row0.py` · `navpanel_column0.py` · `navpanel_detail.py` · `navpanel_icons.py` · `navpanel_icon_registry.py` | Nav-panel OCR — row-0 distance/selection, first-unexplored body by identity, column-0 star-vs-station icon, detail-page button label |
| `target_panel_distance.py` | Right-side cockpit target-panel station-approach distance (km) |
| `escape_vector.py` · `escape_vector_marker.py` | World-space escape-vector sky marker + smack body-kind steer tag |
| `station_menu.py` | Which item is highlighted in the docked in-station menu |
| `debug_overlay.py` | CV-debug overlay **sink** — draws the labeled look-here boxes (fail-soft, off by default) |

## Contract

- **No keys, no motion, no siblings.** Every reader is a pure function of a
  captured frame (plus optional journal grounding). Uncertainty is reported
  honestly so callers in `ed-core` can **fail closed**.
- **Backends degrade gracefully.** The compass reads via a light ONNX model by
  default and falls back to a colour-free OpenCV shape detector that needs no
  model file, so it works regardless of HUD colour / EDHM mods.

## License

**AGPL-3.0-or-later** — declared in this package's `pyproject.toml`. This
package bundles the compass model weights (`model/compass.onnx` / `compass.pt`,
Ultralytics), which are **AGPL-3.0** — the reason the metadata is AGPL. The
colour-free OpenCV fallback needs no weights, so a build that ships without the
model does not attach that obligation. (The repo also carries a root MIT
`LICENSE` file; the per-package metadata is the binding declaration here.)
