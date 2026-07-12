"""[vision] config block: defaults + TOML override."""

from ed_core.config import Config, VisionConfig, load_config


def test_defaults_are_safe_and_off():
    cfg = Config()
    assert isinstance(cfg.vision, VisionConfig)
    assert cfg.vision.enabled is False           # opt-in until calibrated
    assert cfg.vision.backend == "yolo-onnx"     # light path by default
    assert cfg.vision.region == (0, 0, 0, 0)     # uncalibrated sentinel
    # widget-ring fine pass: ON by default (operator decision 2026-06-03),
    # 1080p centre crop. Note: compass alignment itself is still gated by
    # `enabled`/`region`, so this default only matters once vision is on.
    assert cfg.vision.widget_ring_alignment is True
    assert cfg.vision.widget_crop == (510, 240, 900, 600)
    # WIDGET REQUIRED by default (operator 2026-07-12): a launch preflight miss
    # HALTS loudly rather than silently degrading to compass-only.
    assert cfg.vision.widget_ring_on_miss == "required"


def test_toml_overrides_vision(tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text(
        "\n".join([
            "[vision]",
            "enabled = true",
            'backend = "ultralytics"',
            "require_agreement = true",
            "conf_threshold = 0.4",
            "region = [10, 20, 300, 300]",
            "timeout_s = 30.0",
            "widget_ring_alignment = false",  # prove the off-switch (default is on)
            "widget_crop = [0, 0, 1280, 720]",
        ]),
        encoding="utf-8",
    )
    cfg = load_config(toml)
    assert cfg.vision.enabled is True
    assert cfg.vision.backend == "ultralytics"
    assert cfg.vision.require_agreement is True
    assert cfg.vision.conf_threshold == 0.4
    assert cfg.vision.region == (10, 20, 300, 300)  # list coerced to tuple
    assert cfg.vision.timeout_s == 30.0
    assert cfg.vision.widget_ring_alignment is False  # explicitly turned off
    assert cfg.vision.widget_crop == (0, 0, 1280, 720)  # list coerced to tuple


def test_widget_ring_on_miss_required_loads(tmp_path):
    """The new 'required' mode loads (validation accepts it)."""
    toml = tmp_path / "config.toml"
    toml.write_text('[vision]\nwidget_ring_on_miss = "required"\n', encoding="utf-8")
    cfg = load_config(toml)
    assert cfg.vision.widget_ring_on_miss == "required"


def test_widget_ring_on_miss_rejects_garbage(tmp_path):
    """A typo must refuse to launch, not silently pick a behavior."""
    import pytest
    toml = tmp_path / "config.toml"
    toml.write_text('[vision]\nwidget_ring_on_miss = "sometimes"\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(toml)
