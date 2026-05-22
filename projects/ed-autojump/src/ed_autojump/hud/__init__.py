"""HUD detection + GraphicsConfigurationOverride writer + calibration profile."""

from .detect import EdhmDetection, detect_edhm, detect_graphics_override
from .graphics_override import (
    DEFAULT_CYAN_OVERRIDE,
    DEFAULT_MAGENTA_OVERRIDE,
    write_graphics_override,
)
from .calibration import (
    CalibrationProfile,
    default_profile,
    load_profile,
    save_profile,
)

__all__ = [
    "EdhmDetection",
    "detect_edhm",
    "detect_graphics_override",
    "DEFAULT_CYAN_OVERRIDE",
    "DEFAULT_MAGENTA_OVERRIDE",
    "write_graphics_override",
    "CalibrationProfile",
    "default_profile",
    "load_profile",
    "save_profile",
]
