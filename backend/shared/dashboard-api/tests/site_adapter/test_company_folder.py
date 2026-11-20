"""변환기 폴더를 회사 폴더로 (여러 회사 설계 1-4).

`AX_SITE_ADAPTER_DIR`을 정했으면 그대로, 안 정했고 회사 폴더를 켰으면
`<회사 폴더>/catbooks/site_adapter`.
옛 자리(`.data/site_adapter`)에 폴더가 있으면 처음 읽을 때 통째로 옮기고 옛 폴더는 `.moved`로 둔다.
"""

import json
import shutil
from pathlib import Path

import pytest

from ax_agent_template import companies, company_files
from ax_dashboard_api.site_adapter import mapping as m

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / ".data"
    root.mkdir()
    monkeypatch.delenv(m.MAPPING_DIR_ENV, raising=False)
    monkeypatch.setenv(companies.COMPANIES_DIR_ENV, str(root / "companies"))
    monkeypatch.delenv(company_files.FOLDERS_ENV, raising=False)
    company_files.reset_for_tests()
    return root


def test_without_company_folders_default_dir_stays(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(m.MAPPING_DIR_ENV, raising=False)
    monkeypatch.delenv(companies.COMPANIES_DIR_ENV, raising=False)
    assert m.adapter_dir() == m.DEFAULT_DIR


def test_env_dir_is_respected(data: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pinned = tmp_path / "pinned"
    monkeypatch.setenv(m.MAPPING_DIR_ENV, str(pinned))
    (data / "site_adapter").mkdir()
    assert m.adapter_dir() == pinned
    assert (data / "site_adapter").exists()


def test_old_folder_moves_to_catbooks(data: Path) -> None:
    old = data / "site_adapter"
    old.mkdir()
    shutil.copy(FIXTURES / "nubi_mapping.json", old / "mapping.json")
    (old / "bookkeeping.jsonl").write_text("{}\n", encoding="utf-8")
    new = data / "companies" / "catbooks" / "site_adapter"
    assert m.adapter_dir() == new
    assert m.load_mapping() is not None
    assert (new / "bookkeeping.jsonl").exists()
    assert (data / "site_adapter.moved" / "mapping.json").exists() and not old.exists()
    expected = json.loads((FIXTURES / "nubi_mapping.json").read_text(encoding="utf-8"))
    assert json.loads((new / "mapping.json").read_text(encoding="utf-8")) == expected


def test_no_old_folder_reads_new_path(data: Path) -> None:
    assert m.adapter_dir() == data / "companies" / "catbooks" / "site_adapter"
    assert m.load_mapping() is None
    assert not (data / "companies").exists()
