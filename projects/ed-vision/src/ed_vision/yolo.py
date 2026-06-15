"""
YOLO compass backends.

The vendored model is a YOLO26n exported **end-to-end** (`end2end: True`):
its single output is shape (1, 300, 6), each row already decoded as
`[x1, y1, x2, y2, confidence, class_id]`. No anchor grid, no NMS — just
filter rows by confidence and class. Classes (read from the model's own
metadata, never hardcoded): 0=compass, 1=navpoint, 2=navpoint-behind.

`decode_compass` is pure numpy and unit-tested without any model. The
offset it computes is a *ratio* of the navpoint position to the compass
disc, so it's invariant to the letterbox scale/pad — we never have to map
boxes back to original-image pixels.

onnxruntime / opencv imports are deferred into the reader so this module
(and `decode_compass`) import fine in a build without those wheels.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from .compass import CompassRead, offset_from_boxes

# Default detection confidence floor. The dot classes are small objects; the
# model is confident when it sees them. 0.25 is the ultralytics default.
DEFAULT_CONF_THRESHOLD = 0.25


def decode_compass(
    detections: Any,
    *,
    names: Dict[int, str],
    conf_threshold: float = DEFAULT_CONF_THRESHOLD,
) -> CompassRead:
    """End-to-end output rows -> CompassRead.

    `detections` is an array shaped (N, 6) or (1, N, 6) of
    [x1, y1, x2, y2, conf, class_id]. We pick the most-confident `compass`
    box and the most-confident dot (`navpoint` OR `navpoint-behind`); the
    dot's class decides front vs behind.
    """
    arr = np.asarray(detections, dtype=np.float64)
    if arr.ndim == 3:           # squeeze the batch dim: (1, N, 6) -> (N, 6)
        arr = arr[0]
    if arr.ndim != 2 or arr.shape[0] == 0:
        return CompassRead.not_found()

    best_compass: Optional[np.ndarray] = None
    best_compass_conf = -1.0
    best_dot: Optional[np.ndarray] = None
    best_dot_conf = -1.0
    best_dot_in_front = False

    for row in arr:
        conf = float(row[4])
        if conf < conf_threshold:
            continue
        label = names.get(int(row[5]))
        if label == "compass":
            if conf > best_compass_conf:
                best_compass_conf = conf
                best_compass = row
        elif label in ("navpoint", "navpoint-behind"):
            if conf > best_dot_conf:
                best_dot_conf = conf
                best_dot = row
                best_dot_in_front = (label == "navpoint")

    if best_compass is None or best_dot is None:
        return CompassRead.not_found()

    return offset_from_boxes(
        (float(best_compass[0]), float(best_compass[1]),
         float(best_compass[2]), float(best_compass[3])),
        (float(best_dot[0]), float(best_dot[1]),
         float(best_dot[2]), float(best_dot[3])),
        in_front=best_dot_in_front,
        confidence=best_dot_conf,
    )


def letterbox_params(h: int, w: int, size: int = 640):
    """Scale + top-left pad for fitting an (h, w) image into `size` square,
    keeping aspect ratio. Returns (ratio, left_pad, top_pad)."""
    r = min(size / h, size / w)
    nh, nw = int(round(h * r)), int(round(w * r))
    top = (size - nh) // 2
    left = (size - nw) // 2
    return r, left, top


def unletterbox_xyxy(box, ratio: float, left: int, top: int):
    """Map a box from letterbox space back to original-image pixels —
    the inverse of the forward letterbox transform."""
    x1, y1, x2, y2 = box
    return ((x1 - left) / ratio, (y1 - top) / ratio,
            (x2 - left) / ratio, (y2 - top) / ratio)


def _letterbox(image: Any, size: int = 640, color: int = 114):
    """Resize `image` (HxWx3 BGR) into a square `size` canvas, keeping
    aspect ratio and padding with grey — the preprocessing ultralytics
    models expect. Returns the padded image only (offset is scale/pad
    invariant, so the reader doesn't need the ratio/pad back; calibration
    uses `letterbox_params`/`unletterbox_xyxy` for that)."""
    import cv2  # deferred — only the YOLO path needs opencv

    h, w = image.shape[:2]
    r, left, top = letterbox_params(h, w, size)
    nh, nw = int(round(h * r)), int(round(w * r))
    resized = cv2.resize(image, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), color, dtype=image.dtype)
    canvas[top:top + nh, left:left + nw] = resized
    return canvas


def _names_from_metadata(meta_map: Dict[str, str]) -> Dict[int, str]:
    """Parse the ultralytics `names` metadata string (a stringified dict)
    into {id: label}. Falls back to the known compass classes."""
    raw = meta_map.get("names")
    if raw:
        try:
            parsed = ast.literal_eval(raw)
            return {int(k): str(v) for k, v in parsed.items()}
        except (ValueError, SyntaxError):
            pass
    return {0: "compass", 1: "navpoint", 2: "navpoint-behind"}


class YoloOnnxCompassReader:
    """Default (light) backend: onnxruntime on the vendored compass.onnx."""

    def __init__(
        self,
        model_path: str | Path,
        *,
        conf_threshold: float = DEFAULT_CONF_THRESHOLD,
        providers: Optional[list[str]] = None,
    ):
        import onnxruntime as ort  # deferred — only this backend needs it

        self.conf_threshold = conf_threshold
        self._session = ort.InferenceSession(
            str(model_path),
            providers=providers or ["CPUExecutionProvider"],
        )
        inp = self._session.get_inputs()[0]
        self._input_name = inp.name
        # Static export -> shape [1,3,640,640]; fall back to 640 if dynamic.
        self._size = inp.shape[2] if isinstance(inp.shape[2], int) else 640
        self.names = _names_from_metadata(
            dict(self._session.get_modelmeta().custom_metadata_map)
        )

    def _preprocess(self, frame: Any) -> Any:
        import cv2  # deferred

        padded = _letterbox(frame, self._size)
        rgb = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB)
        x = rgb.astype(np.float32) / 255.0
        x = np.transpose(x, (2, 0, 1))[np.newaxis, ...]  # HWC -> 1CHW
        return np.ascontiguousarray(x)

    def read(self, frame: Any) -> CompassRead:
        x = self._preprocess(frame)
        out = self._session.run(None, {self._input_name: x})
        return decode_compass(out[0], names=self.names, conf_threshold=self.conf_threshold)

    def raw_detections(self, frame: Any):
        """All detections as (label, confidence, (x1,y1,x2,y2)) in ORIGINAL
        frame pixels — used by `calibrate-compass` to locate the disc."""
        h, w = frame.shape[:2]
        x = self._preprocess(frame)
        out = self._session.run(None, {self._input_name: x})[0]
        arr = np.asarray(out, dtype=np.float64)
        if arr.ndim == 3:
            arr = arr[0]
        ratio, left, top = letterbox_params(h, w, self._size)
        results = []
        for row in arr:
            conf = float(row[4])
            if conf < self.conf_threshold:
                continue
            label = self.names.get(int(row[5]))
            box = unletterbox_xyxy((row[0], row[1], row[2], row[3]), ratio, left, top)
            results.append((label, conf, box))
        return results
