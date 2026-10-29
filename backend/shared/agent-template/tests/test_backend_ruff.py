"""backend 전체가 ruff check 0건인지 확인한다 (#41)."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[3]


def test_backend_has_no_ruff_findings() -> None:
    if importlib.util.find_spec("ruff") is None:
        pytest.skip("ruff가 설치돼 있지 않다")
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", str(BACKEND_ROOT)],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, f"ruff 결과:\n{result.stdout}\n{result.stderr}"
