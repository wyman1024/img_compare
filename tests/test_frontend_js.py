from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _node() -> str:
    executable = shutil.which("node")
    if executable is None:
        pytest.skip("Node.js is not installed; browser utility tests were not run")
    return executable


def test_all_browser_scripts_are_valid_javascript():
    node = _node()
    scripts = sorted((PROJECT_ROOT / "static" / "js").glob("*.js"))
    assert scripts
    for script in scripts:
        result = subprocess.run(
            [node, "--check", str(script)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr or result.stdout


def test_frontend_pure_utilities():
    result = subprocess.run(
        [_node(), str(PROJECT_ROOT / "tests" / "frontend_utils.test.cjs")],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "frontend utility tests passed" in result.stdout
