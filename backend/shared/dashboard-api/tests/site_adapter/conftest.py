"""사이트 변환기 시험 공용: 누비투어 짝·녹화 파일과 변환기 폴더."""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def adapter_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """누비투어 짝을 깐 변환기 폴더 (`AX_SITE_ADAPTER_DIR`)."""
    folder = tmp_path / "site_adapter"
    folder.mkdir()
    shutil.copy(FIXTURES / "nubi_mapping.json", folder / "mapping.json")
    monkeypatch.setenv("AX_SITE_ADAPTER_DIR", str(folder))
    return folder
