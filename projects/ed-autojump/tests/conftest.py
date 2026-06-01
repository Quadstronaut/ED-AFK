"""Shared pytest fixtures."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


# Vision-dependent test modules need numpy / cv2 from the [cv] extra (not in
# [dev], not in CI's base install). Ignore them at collection time when the
# deps are missing, so CI doesn't error on `import numpy` / `import cv2`. With
# the deps installed (local dev), they run normally.
_missing_numpy = importlib.util.find_spec("numpy") is None
_missing_cv2 = importlib.util.find_spec("cv2") is None

collect_ignore: list[str] = []
if _missing_numpy:
    collect_ignore += [
        "test_calibrate_ring.py",
        "test_letterbox.py",
        "test_ultralytics_reader.py",
        "test_yolo_decode.py",
    ]
if _missing_cv2:
    collect_ignore += [
        "test_cyan_reader.py",
        "test_opencv_reader.py",
    ]


FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def journal_fixtures() -> Path:
    return FIXTURE_DIR / "journals"


@pytest.fixture
def sample_journal(journal_fixtures: Path) -> Path:
    return journal_fixtures / "sample_jump_sequence.log"


@pytest.fixture
def danger_journal(journal_fixtures: Path) -> Path:
    return journal_fixtures / "sample_danger_class.log"


@pytest.fixture
def no_scoop_journal(journal_fixtures: Path) -> Path:
    return journal_fixtures / "sample_no_scoop.log"
