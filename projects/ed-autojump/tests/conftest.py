"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


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
