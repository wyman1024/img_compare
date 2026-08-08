from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture
def app_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Import the application without creating its module-level data in the repo."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "module-default-data"))
    for name in (
        "SHARE_BASE_URL",
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
        "FEISHU_PREVIEW_IMAGE_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    return importlib.import_module("app")


@pytest.fixture
def app(app_module, tmp_path: Path):
    return app_module.create_app(
        {
            "TESTING": True,
            "DATA_DIR": str(tmp_path / "data"),
            "MAX_IMAGE_BYTES": 1024 * 1024,
            "FEISHU_APP_ID": "",
            "FEISHU_APP_SECRET": "",
            "FEISHU_PREVIEW_IMAGE_KEY": "",
        }
    )


@pytest.fixture
def client(app):
    return app.test_client()
