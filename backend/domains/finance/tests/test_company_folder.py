"""재무 저장소를 캣북스 회사 폴더로, 주문 모음은 회사마다 (여러 회사 설계 1-6, 2026-11-20).

파일(`finance_state.json`·`finance_exports/`·`finance_chat.db`):
- 파일별 환경변수(`FINANCE_STATE_FILE`·`FINANCE_EXPORT_DIR`·`FINANCE_CHAT_DB`)를 정했으면 그대로
  (대화는 예전처럼 `FINANCE_STATE_FILE` 옆도 그대로)
- 안 정했고 회사 폴더를 켰으면(`AX_COMPANIES_DIR`) `<회사 폴더>/catbooks/<이름>`.
  옛 자리(`<회사 폴더의 부모>/<이름>`)에 있으면 처음 읽을 때 옮기고 옛 것은 `.moved`
- 둘 다 없으면 예전처럼 저장하지 않는다(메모리)
- `AX_COMPANY_FOLDERS=off`로 옛 경로를 정해 켜면, 옛 경로가 비어 있을 때 회사 폴더 값으로 되살린다
메모리 주문 모음(`OrderStore`): 회사 ID마다 따로. 별칭은 정식 id로, 기본은 캣북스.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ax_agent_template import companies, company_files
from finance_agent import books, exports, persist
from finance_agent.chat import threads


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / ".data"
    root.mkdir()
    for env in ("FINANCE_STATE_FILE", "FINANCE_EXPORT_DIR", "FINANCE_CHAT_DB"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv(companies.COMPANIES_DIR_ENV, str(root / "companies"))
    monkeypatch.delenv(company_files.FOLDERS_ENV, raising=False)
    company_files.reset_for_tests()
    return root


FOLDER = Path("companies") / "catbooks"


def _chat_path() -> str:
    threads.set_store(None)  # type: ignore[arg-type]
    store = threads.get_store()
    try:
        return store._db.execute("PRAGMA database_list").fetchone()[2]
    finally:
        store._db.close()
        threads.set_store(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------- 회사 폴더를 안 켰을 때
def test_without_company_folders_nothing_changes(data: Path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv(companies.COMPANIES_DIR_ENV, raising=False)
    (data / "finance_state.json").write_text("{}", encoding="utf-8")
    assert persist.state_file() is None
    assert exports.export_dir() is None
    assert _chat_path() == ""  # 메모리
    assert (data / "finance_state.json").exists()


def test_env_paths_win(data: Path, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    pinned = tmp_path / "pinned"
    pinned.mkdir()
    monkeypatch.setenv("FINANCE_STATE_FILE", str(pinned / "state.json"))
    monkeypatch.setenv("FINANCE_EXPORT_DIR", str(pinned / "exports"))
    (data / "finance_state.json").write_text("{}", encoding="utf-8")
    assert persist.state_file() == pinned / "state.json"
    assert exports.export_dir() == pinned / "exports"
    assert Path(_chat_path()) == pinned / "finance_chat.db"  # 예전처럼 기록 파일 옆
    assert (data / "finance_state.json").exists()  # 옮기지 않는다


# ---------------------------------------------------------------- 회사 폴더
def test_new_files_go_to_the_company_folder(data: Path) -> None:
    assert persist.state_file() == data / FOLDER / "finance_state.json"
    assert exports.export_dir() == data / FOLDER / "finance_exports"
    assert Path(_chat_path()) == data / FOLDER / "finance_chat.db"
    assert not (data / "finance_state.json").exists()


def test_old_files_move_to_the_company_folder(data: Path) -> None:
    (data / "finance_state.json").write_text('{"version": 1}', encoding="utf-8")
    (data / "finance_exports" / "fin_2026-10").mkdir(parents=True)
    (data / "finance_exports" / "fin_2026-10" / "pnl.csv").write_text("a,b", encoding="utf-8")
    (data / "finance_chat.db").write_bytes(b"")

    assert persist.state_file() == data / FOLDER / "finance_state.json"
    assert exports.export_dir() == data / FOLDER / "finance_exports"
    assert Path(_chat_path()) == data / FOLDER / "finance_chat.db"

    assert (data / FOLDER / "finance_state.json").read_text(encoding="utf-8") == '{"version": 1}'
    assert (data / FOLDER / "finance_exports" / "fin_2026-10" / "pnl.csv").exists()
    for name in ("finance_state.json", "finance_exports", "finance_chat.db"):
        assert (data / f"{name}.moved").exists() and not (data / name).exists()


def test_off_restores_the_old_paths(data: Path, monkeypatch) -> None:  # noqa: ANN001
    folder = data / FOLDER
    (folder / "finance_exports" / "fin_2026-10").mkdir(parents=True)
    (folder / "finance_exports" / "fin_2026-10" / "pnl.csv").write_text("a,b", encoding="utf-8")
    (folder / "finance_state.json").write_text('{"version": 1}', encoding="utf-8")
    monkeypatch.setenv(company_files.FOLDERS_ENV, "off")
    # run_all.sh가 off일 때 옛 경로를 정한다
    monkeypatch.setenv("FINANCE_STATE_FILE", str(data / "finance_state.json"))
    monkeypatch.setenv("FINANCE_EXPORT_DIR", str(data / "finance_exports"))
    assert persist.state_file() == data / "finance_state.json"
    assert exports.export_dir() == data / "finance_exports"
    assert (data / "finance_state.json").read_text(encoding="utf-8") == '{"version": 1}'
    assert (data / "finance_exports" / "fin_2026-10" / "pnl.csv").exists()
    assert (folder / "finance_state.json").exists()  # 회사 폴더 파일은 지우지 않는다


def test_saving_the_state_into_a_new_company_folder(data: Path) -> None:
    from finance_agent.handlers import State
    from finance_agent.sources import MockSources

    path = persist.state_file()
    assert path is not None
    persist.save(State(MockSources()), path)
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == persist.VERSION


# ---------------------------------------------------------------- 메모리 주문 모음
def test_order_stores_are_kept_per_company() -> None:
    books.clear_live_cache()
    cat = books.order_store()
    assert books.order_store("catbooks") is cat
    assert books.order_store("shop-demo") is cat  # 별칭은 캣북스
    other = books.order_store("nubi-tour")
    assert other is not cat
    assert books.order_store("Not OK!") is cat  # 맞지 않는 id는 캣북스
    books.clear_live_cache()
    assert books.order_store() is not cat  # 캐시를 비우면 새 모음
