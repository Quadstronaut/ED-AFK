# Vendored compass-detection model

These weights are reused from **EDAPGui** (https://github.com/SumZer0-git/EDAPGui),
licensed MIT. They are a `yolo26n` object detector trained to find the
Elite Dangerous nav compass and the target navpoint within it.

| File          | Source in EDAPGui                                  | Runtime          |
|---------------|----------------------------------------------------|------------------|
| `compass.onnx`| `Yolo26/compass-model/weights/best.onnx`           | onnxruntime (default) |
| `compass.pt`  | `Yolo26/compass-model/weights/best.pt`             | ultralytics (opt-in)  |

Classes (read at runtime from the model metadata, not hardcoded):
`compass`, `navpoint` (target ahead / filled dot),
`navpoint-behind` (target behind / hollow dot).

See `THIRD_PARTY_NOTICES.md` at the repo root for full attribution.
