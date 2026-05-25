"""
Opt-in heavy backend: full ultralytics runtime on the vendored compass.pt.

Use only if the default onnxruntime path misbehaves in-game (config
backend = "ultralytics"). Pulls in torch. Inference differs from the onnx
path but feeds the SAME `decode_compass`, so alignment behaviour is
identical — only the detector changes.

The ultralytics import is deferred into the constructor so this module
imports fine without torch installed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from .compass import CompassRead
from .yolo import DEFAULT_CONF_THRESHOLD, decode_compass


def _to_np(x: Any) -> np.ndarray:
    """ultralytics returns torch tensors; convert without importing torch."""
    if hasattr(x, "cpu"):
        x = x.cpu()
    if hasattr(x, "numpy"):
        x = x.numpy()
    return np.asarray(x)


def _result_to_detections(result: Any) -> np.ndarray:
    """An ultralytics Results -> (N, 6) [x1,y1,x2,y2,conf,cls] array."""
    boxes = getattr(result, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return np.zeros((0, 6), dtype=np.float32)
    xyxy = _to_np(boxes.xyxy).reshape(-1, 4)
    conf = _to_np(boxes.conf).reshape(-1, 1)
    cls = _to_np(boxes.cls).reshape(-1, 1)
    return np.hstack([xyxy, conf, cls]).astype(np.float32)


class UltralyticsCompassReader:
    """YOLO(compass.pt).predict(...) -> decode_compass."""

    def __init__(
        self,
        model_path: str | Path,
        *,
        conf_threshold: float = DEFAULT_CONF_THRESHOLD,
    ):
        from ultralytics import YOLO  # deferred — only this backend needs torch

        self.conf_threshold = conf_threshold
        self._model = YOLO(str(model_path))
        # model.names is the {id: label} map ultralytics embeds.
        self.names = {int(k): str(v) for k, v in self._model.names.items()}

    def read(self, frame: Any) -> CompassRead:
        results = self._model.predict(frame, verbose=False, conf=self.conf_threshold)
        if not results:
            return CompassRead.not_found()
        r = results[0]
        names = {int(k): str(v) for k, v in getattr(r, "names", self.names).items()}
        dets = _result_to_detections(r)
        return decode_compass(dets, names=names, conf_threshold=self.conf_threshold)
