"""Config layering — config.local.toml merge, .env loading, env overrides."""

import pytest

from ed_core.config import load_config


def _write(path, text):
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# config.local.toml merge
# ---------------------------------------------------------------------------

def test_local_toml_overrides_primary(tmp_path):
    _write(tmp_path / "config.toml", "[overlay]\nport = 5011\n")
    _write(tmp_path / "config.local.toml", "[overlay]\nport = 5012\n")
    cfg = load_config(tmp_path / "config.toml", environ={})
    assert cfg.overlay.port == 5012


def test_local_toml_applies_even_without_primary(tmp_path):
    # The CLI passes a config.toml path whether or not the file exists; the
    # local file beside it must still merge.
    _write(tmp_path / "config.local.toml", "[overlay]\ncv_debug = true\n")
    cfg = load_config(tmp_path / "config.toml", environ={})
    assert cfg.overlay.cv_debug is True


def test_defaults_when_nothing_present(tmp_path):
    cfg = load_config(tmp_path / "config.toml", environ={})
    assert cfg.overlay.cv_debug is True            # ships ON, opt-out (2026-06-13)
    assert cfg.overlay.cv_debug_ttl_s == 2.0


# ---------------------------------------------------------------------------
# ED_AUTOJUMP_* env overrides
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["1", "true", "YES", "On"])
def test_env_bool_true_variants(tmp_path, raw):
    cfg = load_config(tmp_path / "config.toml",
                      environ={"ED_AUTOJUMP_OVERLAY_CV_DEBUG": raw})
    assert cfg.overlay.cv_debug is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "OFF"])
def test_env_bool_false_variants(tmp_path, raw):
    _write(tmp_path / "config.local.toml", "[overlay]\ncv_debug = true\n")
    cfg = load_config(tmp_path / "config.toml",
                      environ={"ED_AUTOJUMP_OVERLAY_CV_DEBUG": raw})
    assert cfg.overlay.cv_debug is False           # env outranks local


def test_env_bool_garbage_raises(tmp_path):
    with pytest.raises(ValueError, match="ED_AUTOJUMP_OVERLAY_CV_DEBUG"):
        load_config(tmp_path / "config.toml",
                    environ={"ED_AUTOJUMP_OVERLAY_CV_DEBUG": "maybe"})


def test_env_int_float_str_coercion(tmp_path):
    cfg = load_config(tmp_path / "config.toml", environ={
        "ED_AUTOJUMP_OVERLAY_PORT": "5099",
        "ED_AUTOJUMP_OVERLAY_KEEPALIVE_S": "2.5",
        "ED_AUTOJUMP_OVERLAY_COLOR": "red",
    })
    assert cfg.overlay.port == 5099
    assert cfg.overlay.keepalive_s == 2.5
    assert cfg.overlay.color == "red"


def test_env_underscore_sections_unambiguous(tmp_path):
    cfg = load_config(tmp_path / "config.toml",
                      environ={"ED_AUTOJUMP_MENU_NAV_KEY_DELAY_MS": "300"})
    assert cfg.menu_nav.key_delay_ms == 300


def test_env_cannot_touch_tuple_fields(tmp_path):
    cfg = load_config(tmp_path / "config.toml",
                      environ={"ED_AUTOJUMP_VISION_REGION": "1,2,3,4"})
    assert tuple(cfg.vision.region) == (0, 0, 0, 0)  # untouched by design


def test_env_outranks_both_tomls(tmp_path):
    _write(tmp_path / "config.toml", "[overlay]\nport = 5011\n")
    _write(tmp_path / "config.local.toml", "[overlay]\nport = 5012\n")
    cfg = load_config(tmp_path / "config.toml",
                      environ={"ED_AUTOJUMP_OVERLAY_PORT": "5013"})
    assert cfg.overlay.port == 5013


# ---------------------------------------------------------------------------
# .env file
# ---------------------------------------------------------------------------

def test_dotenv_loaded_from_config_dir(tmp_path):
    _write(tmp_path / ".env",
           "# comment\nED_AUTOJUMP_OVERLAY_X=99\nQUOTED='ignored-key'\n")
    env: dict = {}
    cfg = load_config(tmp_path / "config.toml", environ=env)
    assert cfg.overlay.x == 99
    assert env["ED_AUTOJUMP_OVERLAY_X"] == "99"


def test_real_env_wins_over_dotenv(tmp_path):
    _write(tmp_path / ".env", "ED_AUTOJUMP_OVERLAY_X=99\n")
    cfg = load_config(tmp_path / "config.toml",
                      environ={"ED_AUTOJUMP_OVERLAY_X": "55"})
    assert cfg.overlay.x == 55
