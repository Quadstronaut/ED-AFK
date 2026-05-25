"""
Vision subsystem — reads the in-cockpit nav compass so the bot can orient
the ship toward the next jump target before engaging the FSD.

The bot is otherwise blind (it presses key scancodes without seeing the
screen). Alignment is the one task that genuinely requires sight: the
nav-compass dot tells us where the target is relative to where we point.

Design (see SPEC §9.2 + the orientation thread):
- `CompassReader` is the interface; it returns a `CompassRead` describing
  the dot's normalized offset from compass centre and whether it is in
  FRONT (filled dot) or BEHIND (hollow dot).
- Backends, in order of preference:
    1. `YoloOnnxCompassReader`   — default; onnxruntime on the vendored
       compass.onnx (yolo26n, classes compass/navpoint/navpoint-behind).
    2. `UltralyticsCompassReader` — opt-in heavy backend (torch) for when
       the light path misbehaves in-game.
    3. `OpenCvCompassReader`      — colour-free shape/contrast fallback,
       always available (no model, no ML).
- `CompositeCompassReader` selects a YOLO backend per config and falls
  back to OpenCV when the model/deps are missing or confidence is low.

Everything in this package degrades gracefully: importing it must NOT
require onnxruntime/opencv/dxcam. Heavy imports are deferred into the
backends that need them, so a bot built without the [vision] extra still
imports and runs (vision simply stays disabled).
"""
