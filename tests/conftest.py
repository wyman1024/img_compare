from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture
def app_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Import the application without creating its module-level data in the repo."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "module-default-data"))
    return importlib.import_module("app")


@pytest.fixture
def app(app_module, tmp_path: Path):
    return app_module.create_app(
        {
            "TESTING": True,
            "DATA_DIR": str(tmp_path / "data"),
            "MAX_IMAGE_BYTES": 1024 * 1024,
        }
    )


@pytest.fixture
def client(app):
    return app.test_client()
