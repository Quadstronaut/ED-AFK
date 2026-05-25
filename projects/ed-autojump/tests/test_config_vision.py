"""[vision] config block: defaults + TOML override."""

from ed_autojump.config import Config, VisionConfig, load_config


def test_defaults_are_safe_and_off():
    cfg = Config()
    assert isinstance(cfg.vision, VisionConfig)
    assert cfg.vision.enabled is False           # opt-in until calibrated
    assert cfg.vision.backend == "yolo-onnx"     # light path by default
    assert cfg.vision.region == (0, 0, 0, 0)     # uncalibrated sentinel


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
