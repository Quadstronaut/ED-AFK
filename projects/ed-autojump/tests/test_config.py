"""Phase 0: config loading."""

from __future__ import annotations

from pathlib import Path

from ed_autojump.config import Config, load_config


def test_default_config_loadable_without_file():
    cfg = load_config(None)
    assert isinstance(cfg, Config)
    assert cfg.routing.efficiency == 60
    assert cfg.exploration.honk is True
    assert cfg.cv.target_resolution == (1920, 1080)


def test_load_config_overrides_from_toml(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text(
        '[routing]\nefficiency = 80\n\n[exploration]\nhonk = false\nfss = "keyboard_sweep"\n',
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.routing.efficiency == 80
    assert cfg.exploration.honk is False
    assert cfg.exploration.fss == "keyboard_sweep"


def test_load_config_missing_file_returns_defaults(tmp_path: Path):
    cfg = load_config(tmp_path / "does-not-exist.toml")
    assert cfg.exploration.honk is True


def test_class_pitch_overrides_default():
    cfg = load_config(None)
    assert cfg.input.class_pitch_overrides["K"] == 2.0
    assert cfg.input.class_pitch_overrides["O"] == 4.0


def test_danger_classes_default_includes_DA_N_H_W():
    cfg = load_config(None)
    s = set(cfg.routing.danger_classes)
    assert "DA" in s
    assert "N" in s
    assert "H" in s
    assert "W" in s
