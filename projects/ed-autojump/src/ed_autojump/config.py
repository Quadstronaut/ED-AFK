"""
Config loader.

Single dataclass-shaped config. Merge order (later wins):

    defaults -> config.toml -> config.local.toml -> ED_AUTOJUMP_* env vars

`config.local.toml` sits next to `config.toml` and is gitignored — the
non-committed place for machine-local overrides (e.g. the operator's
`[overlay] cv_debug = true`). Env overrides `ED_AUTOJUMP_<SECTION>_<KEY>`
work for flat SCALAR keys only (str/int/float/bool); tuple/dict fields are
deliberately env-unreachable — use a TOML layer for those. A `.env` file
next to the config is loaded first (real environment variables win over it).
Defaults match SPEC §13.
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
    expected_ship: str = "mandalay"
    expected_max_jump_range_ly: float = 31.288
    expected_fuel_capacity_t: float = 32.0
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
    # refuel_threshold DELETED 2026-06-06: it sat unwired since v1. The live
    # scoop-trigger knob is `refuel_below` on the scoop_refuel step in
    # arrival.toml (step params are where every other flight tunable lives).
    danger_classes: tuple[str, ...] = DEFAULT_DANGER_CLASSES


@dataclass
class ExplorationConfig:
    honk: bool = True
    fss: str = "off"  # "off" | "keyboard_sweep" | "cv_assisted"
    dss: str = "off"  # "off" | "high_value_only" | "all"
    dss_max_distance_ls: float = 50_000.0
    dss_per_system_cap: int = 4
    dss_tier_threshold: int = 1
    # Body-touring subsystem (body_tour). OPT-IN: enabled=False == byte-
    # identical to the pre-feature jump loop. When on, arrival tours the
    # arrival system's unexplored bodies (SC-assist orbit each, gated on its
    # AutoScan) between the star orbit and target_next_route.
    body_tour_enabled: bool = False
    body_tour_dwell_s: float = 2.0          # post-AutoScan pacing loiter (sleeper, NOT a gate)
    body_tour_max_bodies: int = 5
    body_tour_max_rows: int = 8
    body_tour_orbit_timeout_s: float = 120.0   # per-body BACKSTOP only, never a success gate
    body_tour_min_bodies: int = 0           # only tour a system whose honk BodyCount >= this (0 = every system)
    # IDENTITY targeting (task #45): when on, body_tour reads the NAVIGATION
    # panel (OCR) and targets the next UNEXPLORED body by NAME instead of a
    # blind row walk. OFF by default — needs the [cv] extra (pytesseract +
    # tesseract binary) and a live OCR pass to lock psm/preprocessing.
    # nav_panel_region is (x, y, w, h) @1920x1080, MEASURED from a real frame
    # (the body-name column); see vision/navpanel_reader.DEFAULT_NAV_REGION.
    nav_panel_ocr_enabled: bool = False
    nav_panel_region: tuple[int, int, int, int] = (505, 435, 410, 330)


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
    target_resolution: tuple[int, int] = (1920, 1080)
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

    `profiles` maps the friendly commander name (CmdrOne, CmdrTwo, …)
    to the on-disk `.frontier-<name>.cred` profile slug. Default Account1..4
    matches the existing Sandboxie launch scheme so the same .cred files
    travel between sandboxie and non-sandboxie usage modulo DPAPI binding.
    """

    mel_path: str = ""  # "" = auto-detect (PATH, scoop, common install dirs)
    default_commander: str = "CmdrOne"
    default_auth: str = "frontier"   # "frontier" | "steam"
    default_product: str = "edo"     # "edo" | "edh4"
    default_group: str = "CmdrFour"
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
        "CmdrOne": "account1",
        "CmdrTwo": "account2",
        "CmdrThree": "account3",
        "CmdrFour": "account4",
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
    group_owner_commander: str = "CmdrFour"
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
    align_tol: float = 0.12
    deadzone: float = 0.08
    gain: float = 2.0
    min_press_s: float = 0.10
    max_press_s: float = 0.70
    search_press_s: float = 0.2
    settle_s: float = 1.4
    max_iters: int = 40
    timeout_s: float = 45.0
    # Reads per measurement; >1 enables temporal-median spike rejection.
    align_samples: int = 7
    # Widget-ring FINE alignment (additive after orient_compass). ON by default
    # (operator decision 2026-06-03). Needs the HUD mouse widget in "point"
    # mode. widget_crop is the 1080p centre rect (x, y, w, h).
    widget_ring_alignment: bool = True
    widget_crop: tuple[int, int, int, int] = (510, 240, 900, 600)
    # What a widget MISS does (operator decision 2026-06-06, GitHub issue #1):
    #   "degrade"     (default) — fine pass is skipped (compass-only) and the
    #                 jump proceeds. If we're genuinely off-target the FSD
    #                 charge aborts and autorecovery maneuvers fix it.
    #   "fail_closed" — never jump on an unconfirmed fine-orient: preflight
    #                 warns and the required fine step fails per jump until
    #                 the widget is detectable.
    widget_ring_on_miss: str = "degrade"


@dataclass
class OverlayConfig:
    """EDMCOverlay in-game status overlay (cosmetic; fail-soft).

    The bot writes execution info to EDMCOverlay's TCP server (raw socket,
    127.0.0.1:5010). It first waits `connect_timeout_s` for an already-running
    server (EDMC starts it); if still down and `launch_if_absent`, it locates
    and launches `EDMCOverlay.exe` itself. If neither works it goes quiet — the
    overlay never blocks or crashes a flight.

    exe_path: explicit override to EDMCOverlay.exe; "" = auto-detect
    (%LOCALAPPDATA% → %APPDATA% → fixed-drive sweep). x/y are virtual
    1280x1024 overlay coords; (20,40) is the safe top-left margin.
    """

    enabled: bool = True
    # Mirror the same execution info to the launch terminal (stdout). ON by
    # default so the stream's console is informative; the in-game overlay and
    # the console are independent sinks (either/both can be on).
    console: bool = True
    host: str = "127.0.0.1"
    port: int = 5010
    exe_path: str = ""                  # "" = auto-detect
    connect_timeout_s: float = 30.0     # wait for an already-running server (A)
    launch_if_absent: bool = True       # else launch EDMCOverlay.exe (B)
    launch_settle_s: float = 2.0        # pause after Popen before reconnecting
    launch_connect_timeout_s: float = 10.0
    keepalive_s: float = 4.0            # re-send slots this often (< ttl)
    x: int = 20
    y: int = 40
    color: str = "yellow"
    size: str = "normal"               # "normal" | "large"
    ttl: int = 6                       # seconds; > keepalive_s so it never blinks
    # CV debug boxes (spec 2026-06-10): flash an outlined box over every
    # region a named ScreenGrabber captures, color-coded by detector verdict
    # (white look / green hit / red miss). DEFAULT ON, OPT-OUT (operator
    # directive 2026-06-13): vision data is ALWAYS drawn to the overlay so
    # sizing/location/confidence are continuously verifiable. Opt out with
    # `cv_debug = false` in config.local.toml or ED_AUTOJUMP_OVERLAY_CV_DEBUG=0.
    # Fail-soft: no EDMCOverlay / overlay disabled -> silently no boxes.
    cv_debug: bool = True
    cv_debug_ttl_s: float = 2.0        # flash duration; wire ttl = int(this)


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
    overlay: OverlayConfig = field(default_factory=OverlayConfig)


_SECTIONS = (
    "ship", "routing", "exploration", "safety", "input",
    "binds", "hud", "cv", "eddn", "paths", "launcher", "menu_nav",
    "vision", "nav", "overlay",
)

# Bool env-var convention (case-insensitive). Anything else raises — a typo'd
# flag silently picking a behavior is exactly what the loader must not do.
_ENV_TRUE = frozenset(("1", "true", "yes", "on"))
_ENV_FALSE = frozenset(("0", "false", "no", "off"))


def _merge(section_obj: object, table: dict) -> None:
    """Shallow-merge TOML values into the dataclass section."""
    for key, value in table.items():
        if hasattr(section_obj, key):
            existing = getattr(section_obj, key)
            # Preserve tuple-ness for list-shaped fields.
            if isinstance(existing, tuple) and isinstance(value, list):
                value = tuple(value)
            setattr(section_obj, key, value)


def _load_dotenv(directory: Path, environ) -> None:
    """Minimal `.env` loader: KEY=VALUE lines, `#` comments, optional
    surrounding quotes. Real environment variables WIN over .env values.
    No third-party dependency on purpose."""
    p = Path(directory) / ".env"
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in environ:
            environ[key] = value


def _coerce_env(existing, raw: str, env_name: str):
    """Coerce an env string to the field's existing scalar type, or None for
    non-scalar fields (tuples/dicts are deliberately env-unreachable — they
    would bypass _merge's tuple coercion; use config.local.toml instead).
    bool checks FIRST: bool is an int subclass."""
    if isinstance(existing, bool):
        v = raw.strip().lower()
        if v in _ENV_TRUE:
            return True
        if v in _ENV_FALSE:
            return False
        raise ValueError(
            f"{env_name}: expected a boolean "
            f"({'/'.join(sorted(_ENV_TRUE | _ENV_FALSE))}), got {raw!r}")
    if isinstance(existing, int):
        return int(raw)
    if isinstance(existing, float):
        return float(raw)
    if isinstance(existing, str):
        return raw
    return None


def _apply_env_overrides(cfg: "Config", environ) -> None:
    """ED_AUTOJUMP_<SECTION>_<KEY> -> cfg.<section>.<key> for scalar fields.

    Names are CONSTRUCTED from the known (section, key) pairs — never parsed
    out of the env name — so underscore-bearing sections (menu_nav) and keys
    (key_delay_ms) are unambiguous."""
    for section_name in _SECTIONS:
        section = getattr(cfg, section_name)
        for key, existing in vars(section).items():
            env_name = f"ED_AUTOJUMP_{section_name.upper()}_{key.upper()}"
            raw = environ.get(env_name)
            if raw is None:
                continue
            coerced = _coerce_env(existing, raw, env_name)
            if coerced is not None:
                setattr(section, key, coerced)


def load_config(path: str | Path | None = None, *, environ=None) -> Config:
    """defaults -> config.toml (if `path` exists) -> config.local.toml
    (beside it) -> .env file -> ED_AUTOJUMP_* env overrides. Later wins.

    With path=None the local/.env files are looked up in the cwd — the CLI
    runs from the project dir, so machine-local overrides still apply.
    `environ` is injectable for tests; defaults to os.environ."""
    environ = os.environ if environ is None else environ
    cfg = Config()

    base_dir = Path(".")
    files: list[Path] = []
    if path is not None:
        p = Path(path)
        base_dir = p.parent if str(p.parent) else Path(".")
        if p.is_file():
            files.append(p)
    local = base_dir / "config.local.toml"
    if local.is_file():
        files.append(local)

    for f in files:
        with open(f, "rb") as fh:
            raw = tomllib.load(fh)
        for section_name in _SECTIONS:
            if section_name in raw:
                _merge(getattr(cfg, section_name), raw[section_name])

    _load_dotenv(base_dir, environ)
    _apply_env_overrides(cfg, environ)

    if cfg.vision.widget_ring_on_miss not in ("degrade", "fail_closed"):
        # A typo here would silently pick one behavior — refuse to launch.
        raise ValueError(
            f"[vision].widget_ring_on_miss must be 'degrade' or 'fail_closed', "
            f"got {cfg.vision.widget_ring_on_miss!r}"
        )
    return cfg
