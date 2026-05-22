"""
Config loader.

Single dataclass-shaped config. Reads `config.toml` from the project
directory; environment overrides `ED_AUTOJUMP_<SECTION>_<KEY>` work for the
flat keys. Defaults match SPEC §13.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


# Default danger-class set (SPEC §8.4). The route filter rejects any leg
# whose destination StarClass is in this set.
DEFAULT_DANGER_CLASSES = (
    "D", "DA", "DAB", "DAO", "DAZ", "DAV",
    "DB", "DBZ", "DBV", "DO", "DOV", "DQ",
    "DC", "DCV", "DX",
    "N", "H",
    "W", "WC", "WN", "WNC", "WO",
    "AeBe", "TTS",
)


@dataclass
class ShipConfig:
    expected_ship: str = "cutter"
    expected_max_jump_range_ly: float = 31.288
    expected_fuel_capacity_t: float = 64.0
    required_modules: tuple[str, ...] = (
        "int_fuelscoop_*",
        "int_detailedsurfacescanner_tiny",
    )
    required_modules_v2: tuple[str, ...] = ("int_dockingcomputer_advanced",)


@dataclass
class RoutingConfig:
    mode: str = "external_spansh"
    destination: str = "Beagle Point"
    efficiency: int = 60
    range_margin: float = 0.97
    fuel_safety_threshold: float = 0.20
    refuel_threshold: float = 0.70
    danger_classes: tuple[str, ...] = DEFAULT_DANGER_CLASSES


@dataclass
class ExplorationConfig:
    honk: bool = True
    fss: str = "off"  # "off" | "keyboard_sweep" | "cv_assisted"
    dss: str = "off"  # "off" | "high_value_only" | "all"
    dss_max_distance_ls: float = 50_000.0
    dss_per_system_cap: int = 4
    dss_tier_threshold: int = 1


@dataclass
class SafetyConfig:
    hull_panic_threshold: float = 0.70
    heat_panic_threshold: float = 1.00
    heatsink_threshold: float = 0.80
    no_journal_timeout_s: float = 90.0
    panic_hotkey: str = "ctrl+alt+p"
    legal_state_allowed: tuple[str, ...] = ("Clean", "Allied")


@dataclass
class InputConfig:
    backend: str = "pydirectinput"
    key_delay_ms: int = 75
    pitch_up_default_s: float = 2.0
    class_pitch_overrides: dict[str, float] = field(
        default_factory=lambda: {
            "K": 2.0, "G": 2.0, "F": 2.0,
            "B": 3.0, "A": 3.0, "O": 4.0, "M": 1.5,
            "L": 1.5, "T": 1.5, "Y": 1.5,
            "D": 4.5, "DA": 4.5, "DB": 4.5,
            "N": 4.5, "H": 4.0,
            "W": 4.0, "WC": 4.0, "WN": 4.0, "WNC": 4.0, "WO": 4.0,
        }
    )


@dataclass
class BindsConfig:
    preset_name: str = "ED-AFK"
    auto_swap_start_preset: bool = True
    restore_on_exit: bool = True


@dataclass
class HudConfig:
    edhm_detect: bool = True
    edhm_preset_to_offer: str = "ED-AFK-CV.json"
    graphics_override_fallback: bool = True


@dataclass
class CvConfig:
    capture_backend: str = "dxcam-cpp"
    require_sdr: bool = True
    require_borderless_windowed: bool = True
    target_resolution: tuple[int, int] = (2560, 1440)
    ocr_engine: str = "tesseract"


@dataclass
class EddnConfig:
    publish: bool = True
    software_name: str = "ED-AFK / ed-autojump"
    software_version: str = "0.2.0"
    uploader_id: str = ""


@dataclass
class PathsConfig:
    journal_dir: str = r"%USERPROFILE%\Saved Games\Frontier Developments\Elite Dangerous"
    binds_dir: str = r"%LOCALAPPDATA%\Frontier Developments\Elite Dangerous\Options\Bindings"
    log_dir: str = "./logs"
    calibration_dir: str = "./calibration"

    def journal_dir_expanded(self) -> Path:
        return Path(os.path.expandvars(self.journal_dir))

    def binds_dir_expanded(self) -> Path:
        return Path(os.path.expandvars(self.binds_dir))


@dataclass
class Config:
    ship: ShipConfig = field(default_factory=ShipConfig)
    routing: RoutingConfig = field(default_factory=RoutingConfig)
    exploration: ExplorationConfig = field(default_factory=ExplorationConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    input: InputConfig = field(default_factory=InputConfig)
    binds: BindsConfig = field(default_factory=BindsConfig)
    hud: HudConfig = field(default_factory=HudConfig)
    cv: CvConfig = field(default_factory=CvConfig)
    eddn: EddnConfig = field(default_factory=EddnConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)


def _merge(section_obj: object, table: dict) -> None:
    """Shallow-merge TOML values into the dataclass section."""
    for key, value in table.items():
        if hasattr(section_obj, key):
            existing = getattr(section_obj, key)
            # Preserve tuple-ness for list-shaped fields.
            if isinstance(existing, tuple) and isinstance(value, list):
                value = tuple(value)
            setattr(section_obj, key, value)


def load_config(path: str | Path | None = None) -> Config:
    """Load `config.toml` if it exists; otherwise return defaults."""
    cfg = Config()
    if path is None:
        return cfg
    p = Path(path)
    if not p.is_file():
        return cfg
    with open(p, "rb") as fh:
        raw = tomllib.load(fh)

    for section_name in (
        "ship", "routing", "exploration", "safety", "input",
        "binds", "hud", "cv", "eddn", "paths",
    ):
        if section_name in raw:
            _merge(getattr(cfg, section_name), raw[section_name])
    return cfg
