"""Shared test fixtures."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import db


@pytest.fixture
def mem_db(monkeypatch, tmp_path):
    """Redirect db.DB_PATH to a fresh per-test file so tests are isolated."""
    test_db = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", test_db)
    db.init_db()
    return test_db
