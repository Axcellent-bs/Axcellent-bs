"""기획 대화·노션 연결 파일을 회사 폴더로 (여러 회사 설계 1-6, 2026-11-20).

`PLANNING_STATE_DIR`가 없으면 예전처럼 메모리만(파일 없음). 있으면:
- 회사 폴더를 켰으면(`AX_COMPANIES_DIR`) `<회사 폴더>/catbooks/`의
  `planning_chat.db`·`notion_token.json`.
  옛 자리(`PLANNING_STATE_DIR/<이름>`)에 있으면 처음 열 때 옮기고 옛 것은 `.moved`
- 회사 폴더를 안 켰으면 옛 자리 그대로
- `AX_COMPANY_FOLDERS=off`면 옛 자리. 옛 자리가 비어 있으면 회사 폴더 값으로 되살린다
업무 흐름 상태 파일(workflow-core)은 이번 범위가 아니라 `PLANNING_STATE_DIR`에 그대로 둔다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ax_agent_template import companies, company_files
from planning_agent import company_folder, notion_store
from planning_agent.chat import threads

TOKEN = "ntn_test_1234567890abcd"


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / ".data"
    root.mkdir()
    monkeypatch.setenv("PLANNING_STATE_DIR", str(root))
    monkeypatch.setenv(companies.COMPANIES_DIR_ENV, str(root / "companies"))
    monkeypatch.delenv(company_files.FOLDERS_ENV, raising=False)
    company_files.reset_for_tests()
    notion_store.reset_for_tests()
    yield root
    notion_store.reset_for_tests()


FOLDER = Path("companies") / "catbooks"


def test_without_a_state_dir_nothing_is_stored(data: Path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("PLANNING_STATE_DIR", raising=False)
    assert company_folder.state_path("planning_chat.db") == ""


def test_without_company_folders_the_state_dir_is_used(data: Path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv(companies.COMPANIES_DIR_ENV, raising=False)
    assert company_folder.state_path("planning_chat.db") == str(data / "planning_chat.db")


def test_new_files_go_to_the_company_folder(data: Path) -> None:
    path = company_folder.state_path("planning_chat.db")
    assert path == str(data / FOLDER / "planning_chat.db")
    assert Path(path).parent.is_dir()  # SQLite가 파일을 만들 수 있게 폴더는 있다
    threads.ChatStore(path).create("새 대화", "yejin")
    assert (data / FOLDER / "planning_chat.db").exists()
    assert not (data / "planning_chat.db").exists()


def test_an_old_chat_db_moves_and_keeps_its_threads(data: Path) -> None:
    old = data / "planning_chat.db"
    before = threads.ChatStore(str(old))
    before.create("예전 대화", "yejin")
    before._db.close()  # 서버가 켜기 전(아직 아무도 열지 않음)과 같게
    path = company_folder.state_path("planning_chat.db", extras=company_folder.SQLITE_EXTRAS)
    assert path == str(data / FOLDER / "planning_chat.db")
    assert [t["title"] for t in threads.ChatStore(path).list_all()] == ["예전 대화"]
    assert (data / "planning_chat.db.moved").exists() and not old.exists()


def test_a_hot_sqlite_journal_moves_with_the_db(data: Path) -> None:
    (data / "planning_chat.db").write_bytes(b"db")
    (data / "planning_chat.db-journal").write_bytes(b"journal")
    company_folder.state_path("planning_chat.db", extras=company_folder.SQLITE_EXTRAS)
    assert (data / FOLDER / "planning_chat.db-journal").read_bytes() == b"journal"
    assert not (data / "planning_chat.db-journal").exists()


def test_the_chat_store_opens_in_the_company_folder(data: Path) -> None:
    threads.set_store(None)  # type: ignore[arg-type]
    try:
        store = threads.get_store()
        store.create("새 대화", "yejin")
        assert (data / FOLDER / "planning_chat.db").exists()
    finally:
        store._db.close()
        threads.set_store(None)  # type: ignore[arg-type]


def test_the_notion_token_moves_and_is_read(data: Path) -> None:
    (data / "notion_token.json").write_text(
        json.dumps({"token": TOKEN, "workspace": "캣북스", "checked_at": ""}), encoding="utf-8"
    )
    assert notion_store.saved_token() == TOKEN
    assert (data / FOLDER / "notion_token.json").exists()
    assert (data / "notion_token.json.moved").exists()


def test_a_new_notion_token_is_saved_in_the_company_folder(data: Path) -> None:
    notion_store.set_token(TOKEN, "캣북스")
    saved = json.loads((data / FOLDER / "notion_token.json").read_text(encoding="utf-8"))
    assert saved["token"] == TOKEN
    assert not (data / "notion_token.json").exists()


def test_off_restores_the_old_place(data: Path, monkeypatch) -> None:  # noqa: ANN001
    (data / FOLDER).mkdir(parents=True)
    before = threads.ChatStore(str(data / FOLDER / "planning_chat.db"))
    before.create("회사 폴더 대화", "y")
    before._db.close()
    monkeypatch.setenv(company_files.FOLDERS_ENV, "off")
    path = company_folder.state_path("planning_chat.db", extras=company_folder.SQLITE_EXTRAS)
    assert path == str(data / "planning_chat.db")
    assert [t["title"] for t in threads.ChatStore(path).list_all()] == ["회사 폴더 대화"]
    assert (data / FOLDER / "planning_chat.db").exists()  # 회사 폴더 파일은 지우지 않는다
