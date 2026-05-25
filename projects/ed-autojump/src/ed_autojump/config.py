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
    # If StartJump doesn't follow within this many seconds of pressing
    # HyperSuperCombination, force-clear the debounce flag and try again.
    # 30s covers normal jump-charge time + a generous slack for slow disks.
    engagement_debounce_timeout_s: float = 30.0


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
class LauncherConfig:
    """min-ed-launcher invocation defaults + commander↔profile mapping.

    `profiles` maps the friendly commander name (Duvrazh, Bistronaut, …)
    to the on-disk `.frontier-<name>.cred` profile slug. Default Account1..4
    matches the existing Sandboxie launch scheme so the same .cred files
    travel between sandboxie and non-sandboxie usage modulo DPAPI binding.
    """

    mel_path: str = ""  # "" = auto-detect (PATH, scoop, common install dirs)
    default_commander: str = "Duvrazh"
    default_auth: str = "frontier"   # "frontier" | "steam"
    default_product: str = "edo"     # "edo" | "edh4"
    default_group: str = "Quadstronaut"
    autorun: bool = True
    autoquit: bool = True
    skip_install_prompt: bool = True
    dryrun_timeout_s: float = 10.0   # pre-flight auth check — catches Console.ReadLine() hang
    launch_timeout_s: float = 120.0  # max wait for main menu after spawn
    # "Menu is interactive" is signalled by ED's audio going non-silent. The
    # intro cutscene emits only a ~0.1s blip then silence, so we require the
    # audio to stay non-silent for this many seconds continuously before
    # treating it as menu music — a brief blip never qualifies.
    menu_audio_sustain_s: float = 2.0
    # After Fileheader fires in the journal, wait this many seconds for the
    # main menu UI to become interactive before assuming ready. Lacking a
    # journal event that tracks "menu is usable" (Music{MainMenu} only fires
    # when music is on; no other event marks UI-interactive), a fixed delay
    # is the safe proxy.
    post_fileheader_wait_s: float = 10.0
    profiles: dict[str, str] = field(default_factory=lambda: {
        "Duvrazh": "account1",
        "Bistronaut": "account2",
        "Tristronaut": "account3",
        "Quadstronaut": "account4",
    })


@dataclass
class MenuNavConfig:
    """Main-menu navigation parameters (Continue → PG → group → Launch).

    `calibration` is populated by `ed-autojump calibrate-menu` — keyed by
    commander, each entry holds the press counts the bot must send to
    reach Private Group then the saved group entry. Until calibrated for
    a commander, the bot refuses to drive their menu (raises).

    `group_owner_commander` skips the select-group step: the group owner
    enters PG mode and lands directly in their own lobby.
    """

    enabled: bool = False  # opt-in until calibration done
    post_main_menu_buffer_s: float = 3.0
    key_delay_ms: int = 250
    load_game_timeout_s: float = 180.0
    dismiss_dialogs: bool = True  # send Space+Escape after main-menu detect
    group_owner_commander: str = "Quadstronaut"
    calibration: dict[str, dict[str, int]] = field(default_factory=dict)


@dataclass
class NavConfig:
    """In-system navigation robustness.

    `retarget_route_before_engage`: press TargetNextRouteSystem (bound to H
    in the ED-AFK preset) before each engage so the next route star is
    locked deterministically — no nav-panel scrolling — and the compass has
    a target to align to. Harmless to re-press. NOTE (verify in flight): if a
    given build *cycles* the route target forward on each press, this could
    over-advance — disable it then.

    Supercruise Assist (throttle-mode) groundwork — OFF until the docking /
    orbit flow that uses it is built (Phase 9/10). ED exposes NO keybind for
    Supercruise Assist, so a key can't toggle it; the bot relies on the
    in-game setting "Supercruise Assist = engage on blue-zone throttle". With
    that set, the engage is just throttling into the blue zone with a target
    locked (`sc_assist_throttle_action`).
    """

    retarget_route_before_engage: bool = True
    supercruise_assist: bool = False
    sc_assist_throttle_action: str = "SetSpeed75"


@dataclass
class EscapeConfig:
    """Post-FSDJump star escape configuration.

    escape_mode:
        "brightness" (default) — vision-sensed sun-avoid: pitch up until the
          bright star clears the center screen region, then compass-align.
        "sc_assist" — Supercruise-Assist orbital framework (not live-validated;
          requires SC Assist module, throttle-mode setting, Hyperspace Dethrottle).
        "blind" — legacy fixed-pitch macro from perform_star_escape() (fallback).

    sun_region: (x, y, w, h) of the center-screen capture region.
        (0,0,0,0) => compute center 40%×38% of the primary screen at runtime.
    """

    escape_mode: str = "brightness"    # "brightness" | "sc_assist" | "blind"
    sun_bright_thresh: int = 125       # grayscale pixel value threshold (EDAPGui default)
    sun_clear_frac: float = 0.05       # bright fraction below which the star is "clear"
    sun_pitch_hold_s: float = 0.3      # PitchUpButton hold duration per iteration
    sun_timeout_s: float = 8.0         # abort after this many seconds
    sun_region: tuple = (0, 0, 0, 0)   # (x,y,w,h); (0,0,0,0) => auto center 40%x38%
    # Fly-clear phase: after the star clears center, throttle AWAY from it to put
    # distance between ship and star BEFORE turning toward the (behind-star) target.
    # Without this, aligning to the target points the nose back at the star and the
    # throttle drives straight into it.
    clear_throttle: str = "SetSpeed100"  # throttle action while flying clear (full ahead)
    clear_s: float = 8.0                 # seconds to fly away from the star
    clear_reenter_frac: float = 0.20     # if brightness exceeds this mid-clear, pitch up again
    clear_step_s: float = 0.5            # poll/step interval during the clear phase


@dataclass
class VisionConfig:
    """Nav-compass alignment (orient the ship toward the next target).

    Disabled by default until `ed-autojump calibrate-compass` records the
    on-screen compass region — like [menu_nav], the bot won't drive blind.

    backend: "yolo-onnx" (default, light onnxruntime) | "ultralytics"
    (opt-in, heavy torch) | "opencv" (colour-free, no model). The OpenCV
    reader is always the fallback regardless of backend.

    region: (x, y, w, h) screen rect to capture for the compass. The
    sentinel (0,0,0,0) means "uncalibrated" — vision stays off until set.
    Empty model_onnx/model_pt mean "use the vendored weights".
    """

    enabled: bool = False
    backend: str = "yolo-onnx"
    capture_backend: str = "gdi"  # gdi default; dxcam available as opt-in
    model_onnx: str = ""   # "" -> vendored vision/model/compass.onnx
    model_pt: str = ""     # "" -> vendored vision/model/compass.pt
    conf_threshold: float = 0.25
    require_agreement: bool = False
    agree_tol: float = 0.2
    region: tuple[int, int, int, int] = (0, 0, 0, 0)
    # Half-extent of the compass disc in pixels; 0 = derive from the crop at
    # read-time. Set this after calibrating the compass region capture rect.
    compass_radius: float = 0.0
    # Closed-loop tunables (all overridable from config for in-flight tuning).
    # Defaults validated live 2026-05-24: dominant-axis + behind-flip law,
    # long settle for momentum decay, hard drive (high gain + max_press).
    align_tol: float = 0.15
    deadzone: float = 0.10
    gain: float = 2.0
    min_press_s: float = 0.10
    max_press_s: float = 0.70
    search_press_s: float = 0.2
    settle_s: float = 1.4
    max_iters: int = 40
    timeout_s: float = 45.0
    # Reads per measurement; >1 enables temporal-median spike rejection.
    align_samples: int = 7


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
    launcher: LauncherConfig = field(default_factory=LauncherConfig)
    menu_nav: MenuNavConfig = field(default_factory=MenuNavConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    nav: NavConfig = field(default_factory=NavConfig)
    escape: EscapeConfig = field(default_factory=EscapeConfig)


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
        "binds", "hud", "cv", "eddn", "paths", "launcher", "menu_nav",
        "vision", "nav", "escape",
    ):
        if section_name in raw:
            _merge(getattr(cfg, section_name), raw[section_name])
    return cfg
