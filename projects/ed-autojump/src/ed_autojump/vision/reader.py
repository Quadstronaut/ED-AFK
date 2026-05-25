"""
Composite compass reader + the factory that builds it from config.

Strategy:
  - A *primary* YOLO backend (onnxruntime by default, ultralytics opt-in).
  - An OpenCV colour-free *fallback* that's always available.
  - If the primary sees nothing on a frame, try the fallback.
  - If `require_agreement`, demand the two backends agree (same front/behind,
    offsets close) before trusting a read — disagreement returns
    `not_found()`, which the align loop treats as "don't act". That makes
    vision uncertainty fail *safe* (no misaligned jump) rather than risky.

The factory degrades gracefully: if onnxruntime/torch or the weights are
missing, the primary is silently dropped and the OpenCV fallback carries
on, so the bot never crashes for lack of a model.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from .compass import CompassRead, CompassReader

log = logging.getLogger(__name__)

# Vendored default model location (package data).
_MODEL_DIR = Path(__file__).resolve().parent / "model"
DEFAULT_ONNX_PATH = _MODEL_DIR / "compass.onnx"
DEFAULT_PT_PATH = _MODEL_DIR / "compass.pt"


class CompositeCompassReader:
    def __init__(
        self,
        *,
        primary: Optional[CompassReader],
        fallback: Optional[CompassReader],
        require_agreement: bool = False,
        agree_tol: float = 0.2,
    ):
        self.primary = primary
        self.fallback = fallback
        self.require_agreement = require_agreement
        self.agree_tol = agree_tol

    def read(self, frame: Any) -> CompassRead:
        if self.primary is None:
            return self.fallback.read(frame) if self.fallback else CompassRead.not_found()

        primary = self.primary.read(frame)

        if self.require_agreement and self.fallback is not None:
            return self._reconcile(primary, self.fallback.read(frame))

        if primary.found:
            return primary
        # Primary saw nothing — give the fallback a chance.
        return self.fallback.read(frame) if self.fallback else primary

    def _reconcile(self, primary: CompassRead, fallback: CompassRead) -> CompassRead:
        if not primary.found and not fallback.found:
            return CompassRead.not_found()
        if primary.found and not fallback.found:
            return primary          # trust the primary's precise read
        if fallback.found and not primary.found:
            return fallback
        # Both found: they must agree to be trusted.
        if primary.in_front != fallback.in_front:
            return CompassRead.not_found()
        dist = ((primary.offset_x - fallback.offset_x) ** 2
                + (primary.offset_y - fallback.offset_y) ** 2) ** 0.5
        if dist > self.agree_tol:
            return CompassRead.not_found()
        return primary


def build_compass_reader(
    *,
    backend: str = "yolo-onnx",
    onnx_path: str | Path = DEFAULT_ONNX_PATH,
    pt_path: str | Path = DEFAULT_PT_PATH,
    conf_threshold: float = 0.25,
    require_agreement: bool = False,
    agree_tol: float = 0.2,
    opencv_kwargs: Optional[dict] = None,
    compass_radius: float = 0.0,
) -> CompositeCompassReader:
    """Construct the composite reader. Never raises for a missing model or
    absent ML deps — it logs and drops the primary, leaving the OpenCV
    fallback.

    compass_radius: passed to CyanDotReader as its disc half-extent in pixels.
    0 means derive from the frame at read-time.
    """
    fallback = _try_build_opencv(opencv_kwargs or {})
    primary: Optional[CompassReader] = None

    if backend == "opencv":
        primary = None
    elif backend == "cyan":
        primary = _try_build_cyan(compass_radius)
    elif backend == "ultralytics":
        primary = _try_build_ultralytics(pt_path, conf_threshold)
    else:  # "yolo-onnx" (default) or anything unrecognized
        primary = _try_build_onnx(onnx_path, conf_threshold)

    return CompositeCompassReader(
        primary=primary,
        fallback=fallback,
        require_agreement=require_agreement,
        agree_tol=agree_tol,
    )


def _try_build_onnx(path: str | Path, conf: float) -> Optional[CompassReader]:
    try:
        from .yolo import YoloOnnxCompassReader
        return YoloOnnxCompassReader(path, conf_threshold=conf)
    except Exception as e:  # noqa: BLE001 — deps/model may be absent; degrade.
        log.warning("YOLO onnx backend unavailable (%s); using OpenCV fallback", e)
        return None


def _try_build_ultralytics(path: str | Path, conf: float) -> Optional[CompassReader]:
    try:
        from .ultralytics_reader import UltralyticsCompassReader
        return UltralyticsCompassReader(path, conf_threshold=conf)
    except Exception as e:  # noqa: BLE001
        log.warning("ultralytics backend unavailable (%s); using OpenCV fallback", e)
        return None


def _try_build_cyan(radius: float) -> Optional[CompassReader]:
    try:
        from .cyan_reader import CyanDotReader
        return CyanDotReader(center=None, radius=radius)
    except Exception as e:  # noqa: BLE001
        log.warning("Cyan backend unavailable (%s); using OpenCV fallback", e)
        return None


def _try_build_opencv(kwargs: dict) -> Optional[CompassReader]:
    try:
        from .opencv_reader import OpenCvCompassReader
        return OpenCvCompassReader(**kwargs)
    except Exception as e:  # noqa: BLE001 — opencv/numpy may be absent.
        log.warning("OpenCV fallback unavailable (%s); vision disabled", e)
        return None
