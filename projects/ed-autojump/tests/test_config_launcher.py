"""LauncherConfig + MenuNavConfig — defaults, TOML overrides, profiles."""

from __future__ import annotations

from pathlib import Path

from ed_autojump.config import LauncherConfig, MenuNavConfig, load_config


def test_launcher_config_defaults():
    cfg = LauncherConfig()
    assert cfg.default_commander == "Duvrazh"
    assert cfg.default_auth == "frontier"
    assert cfg.default_product == "edo"
    assert cfg.default_group == "Quadstronaut"
    assert cfg.dryrun_timeout_s > 0
    assert "Duvrazh" in cfg.profiles
    assert cfg.profiles["Duvrazh"] == "account1"
    assert cfg.profiles["Quadstronaut"] == "account4"


def test_menu_nav_config_defaults():
    cfg = MenuNavConfig()
    # Default opt-out — until calibration, the launcher only launches the
    # game and stops at the main menu so the operator can take over.
    assert cfg.enabled is False
    assert cfg.post_main_menu_buffer_s >= 2.0
    assert cfg.group_owner_commander == "Quadstronaut"
    assert cfg.calibration == {}


def test_load_config_picks_up_launcher_section(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text("""
[launcher]
default_commander = "Bistronaut"
default_auth = "steam"
mel_path = "G:/Custom/MEL.exe"

[launcher.profiles]
Duvrazh = "primary"
Newcommander = "account5"
""", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.launcher.default_commander == "Bistronaut"
    assert cfg.launcher.default_auth == "steam"
    assert cfg.launcher.mel_path == "G:/Custom/MEL.exe"
    assert cfg.launcher.profiles["Duvrazh"] == "primary"
    assert cfg.launcher.profiles["Newcommander"] == "account5"


def test_load_config_picks_up_menu_nav_calibration(tmp_path: Path):
    """Per-commander calibrated press counts come from nested TOML tables."""
    p = tmp_path / "config.toml"
    p.write_text("""
[menu_nav]
enabled = true
key_delay_ms = 400

[menu_nav.calibration.Duvrazh]
down_to_private_group = 1
down_to_quadstronaut_in_list = 2

[menu_nav.calibration.Quadstronaut]
down_to_private_group = 1
""", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.menu_nav.enabled is True
    assert cfg.menu_nav.key_delay_ms == 400
    assert cfg.menu_nav.calibration["Duvrazh"]["down_to_private_group"] == 1
    assert cfg.menu_nav.calibration["Duvrazh"]["down_to_quadstronaut_in_list"] == 2
    assert cfg.menu_nav.calibration["Quadstronaut"]["down_to_private_group"] == 1
    # Unmentioned key = not present (not zero, not error — calibrator must set it).
    assert "down_to_quadstronaut_in_list" not in cfg.menu_nav.calibration["Quadstronaut"]
