"""Phase 6: HUD detection + GraphicsOverride writer + calibration."""

from __future__ import annotations

from pathlib import Path

import pytest

from ed_autojump.hud import (
    CalibrationProfile,
    DEFAULT_CYAN_OVERRIDE,
    DEFAULT_MAGENTA_OVERRIDE,
    default_profile,
    detect_edhm,
    load_profile,
    save_profile,
    write_graphics_override,
)


# --- EDHM detection -------------------------------------------------------


def test_detect_edhm_finds_supplied_ui_file(tmp_path: Path):
    ui = tmp_path / "EDHM-UI-V3.exe"
    ui.write_text("fake exe", encoding="utf-8")
    d = detect_edhm(ui_path=ui, elite_install_dir=None)
    assert d.ui_installed is True
    assert d.ui_path == ui
    assert d.dll_installed is False
    assert d.dll_path is None


def test_detect_edhm_finds_d3d11_dll_in_elite_dir(tmp_path: Path):
    elite = tmp_path / "elite-dangerous-64"
    elite.mkdir()
    dll = elite / "d3d11.dll"
    dll.write_bytes(b"\x00")
    d = detect_edhm(ui_path=tmp_path / "no-ui.exe", elite_install_dir=elite)
    assert d.dll_installed is True
    assert d.dll_path == dll


def test_detect_edhm_negative_when_neither_present(tmp_path: Path):
    d = detect_edhm(
        ui_path=tmp_path / "no-ui.exe",
        elite_install_dir=tmp_path / "no-elite",
    )
    assert d.ui_installed is False
    assert d.dll_installed is False


# --- GraphicsConfigurationOverride writer --------------------------------


def test_write_override_refuses_without_consent(tmp_path: Path):
    dest = tmp_path / "GraphicsConfigurationOverride.xml"
    with pytest.raises(PermissionError):
        write_graphics_override(consent=False, dest_path=dest)
    assert not dest.exists()


def test_write_override_cyan_palette(tmp_path: Path):
    dest = tmp_path / "GraphicsConfigurationOverride.xml"
    result = write_graphics_override(consent=True, dest_path=dest, palette="cyan")
    assert result == dest
    text = dest.read_text(encoding="utf-8")
    # Confirm specific matrix lines from the cyan preset.
    assert "<MatrixRed>   0, 1, 0 </MatrixRed>" in text
    assert "<MatrixBlue>  1, 0, 0 </MatrixBlue>" in text


def test_write_override_magenta_palette(tmp_path: Path):
    dest = tmp_path / "GraphicsConfigurationOverride.xml"
    write_graphics_override(consent=True, dest_path=dest, palette="magenta")
    assert "MatrixGreen> 0, 0, 1" in dest.read_text(encoding="utf-8")


def test_write_override_creates_backup(tmp_path: Path):
    dest = tmp_path / "GraphicsConfigurationOverride.xml"
    dest.write_text("user content\n", encoding="utf-8")
    write_graphics_override(consent=True, dest_path=dest)
    backup = dest.with_suffix(".xml.ed-afk.bak")
    assert backup.is_file()
    assert backup.read_text(encoding="utf-8") == "user content\n"


def test_write_override_invalid_palette_raises(tmp_path: Path):
    with pytest.raises(ValueError):
        write_graphics_override(
            consent=True,
            dest_path=tmp_path / "x.xml",
            palette="rainbow",
        )


def test_write_override_creates_parent_dir(tmp_path: Path):
    dest = tmp_path / "nested/dir/GraphicsConfigurationOverride.xml"
    write_graphics_override(consent=True, dest_path=dest)
    assert dest.is_file()


def test_cyan_preset_matches_spec_xml():
    """SPEC §6.4 specifies the exact override XML. Don't drift."""
    assert "<MatrixRed>   0, 1, 0 </MatrixRed>" in DEFAULT_CYAN_OVERRIDE
    assert "<MatrixGreen> 0, 0, 1 </MatrixGreen>" in DEFAULT_CYAN_OVERRIDE
    assert "<MatrixBlue>  1, 0, 0 </MatrixBlue>" in DEFAULT_CYAN_OVERRIDE


# --- Calibration profile --------------------------------------------------


def test_default_profile_has_expected_resolution():
    p = default_profile()
    assert p.screen_w == 2560 and p.screen_h == 1440
    assert p.hdr_active is False


def test_calibration_round_trip(tmp_path: Path):
    p = CalibrationProfile(
        hud_primary_hsv=(178, 245, 220),
        hud_secondary_hsv=(200, 200, 200),
        background_value_max=25,
        screen_w=1920,
        screen_h=1080,
        hdr_active=True,
        rois_relative={"fss": (0.1, 0.2, 0.5, 0.6)},
    )
    path = tmp_path / "profile.json"
    save_profile(p, path)

    loaded = load_profile(path)
    assert loaded.hud_primary_hsv == (178, 245, 220)
    assert loaded.screen_w == 1920
    assert loaded.hdr_active is True
    assert loaded.rois_relative["fss"] == (0.1, 0.2, 0.5, 0.6)
    assert loaded.schema_version == 1
