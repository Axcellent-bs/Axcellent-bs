"""형상관리 연결 파일을 회사 폴더로 (여러 회사 설계 1-6, 2026-11-20).

- `PLANNING_VCS_STORE`를 정했으면 그 경로 그대로
- 안 정했고 회사 폴더를 켰으면(`AX_COMPANIES_DIR`) `<회사 폴더>/catbooks/vcs_connections.json`.
  옛 자리(`<회사 폴더의 부모>/vcs_connections.json`)에 있으면 처음 읽을 때 옮기고 옛 것은 `.moved`
- `AX_COMPANY_FOLDERS=off`로 옛 경로를 정해 켜면, 옛 경로가 비어 있을 때 회사 폴더 값으로 되살린다
- 둘 다 없으면 저장하지 않는다(지금 동작)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ax_agent_template import companies, company_files
from dev_agent import vcs

pytestmark = pytest.mark.role("dev")

ROW = {
    "id": "conn-1",
    "kind": "github",
    "repo": "acme/shop",
    "base_url": "",
    "base_branch": "dev",
    "description": "",
    "paths": [],
    "mount": "",
    "source": "screen",
    "token": "ghp_testtoken1234",
    "auth": "token",
    "app_id": "",
    "installation_id": "",
    "private_key": "",
    "app_ref": "",
}


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / ".data"
    root.mkdir()
    monkeypatch.delenv("PLANNING_VCS_STORE", raising=False)
    monkeypatch.setenv(companies.COMPANIES_DIR_ENV, str(root / "companies"))
    monkeypatch.delenv(company_files.FOLDERS_ENV, raising=False)
    company_files.reset_for_tests()
    vcs._screen = None
    vcs.set_vcs(None)
    yield root
    vcs._screen = None
    vcs.set_vcs(None)


NEW = Path("companies") / "catbooks" / "vcs_connections.json"


def test_without_company_folders_nothing_is_stored(data: Path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv(companies.COMPANIES_DIR_ENV, raising=False)
    assert vcs._store_path() == ""


def test_the_env_path_wins(data: Path, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    pinned = tmp_path / "pinned.json"
    monkeypatch.setenv("PLANNING_VCS_STORE", str(pinned))
    (data / "vcs_connections.json").write_text(json.dumps([ROW]), encoding="utf-8")
    assert vcs._store_path() == str(pinned)
    assert (data / "vcs_connections.json").exists()  # 옮기지 않는다


def test_an_old_file_moves_and_is_read(data: Path) -> None:
    (data / "vcs_connections.json").write_text(json.dumps([ROW]), encoding="utf-8")
    assert vcs.load_saved() == 1
    assert (data / NEW).exists()
    assert (data / "vcs_connections.json.moved").exists()
    assert not (data / "vcs_connections.json").exists()


def test_saving_without_an_old_file_writes_the_company_folder(data: Path) -> None:
    vcs.upsert_connection(kind="github", repo="acme/shop", token="ghp_testtoken1234")
    rows = json.loads((data / NEW).read_text(encoding="utf-8"))
    assert [r["repo"] for r in rows] == ["acme/shop"]
    assert not (data / "vcs_connections.json").exists()


def test_off_restores_the_old_path_from_the_company_folder(data: Path, monkeypatch) -> None:  # noqa: ANN001
    (data / NEW).parent.mkdir(parents=True)
    (data / NEW).write_text(json.dumps([ROW]), encoding="utf-8")
    old = data / "vcs_connections.json"
    monkeypatch.setenv(company_files.FOLDERS_ENV, "off")
    monkeypatch.setenv("PLANNING_VCS_STORE", str(old))  # run_all.sh가 off일 때 옛 경로를 정한다
    assert vcs.load_saved() == 1
    assert json.loads(old.read_text(encoding="utf-8"))[0]["repo"] == "acme/shop"
    assert (data / NEW).exists()  # 회사 폴더 파일은 지우지 않는다
