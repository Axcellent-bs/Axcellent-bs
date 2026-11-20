"""인프라 저장소의 회사 칸·회사 폴더 (여러 회사 설계 1-6, 2026-11-20).

- 대화(`infra_chat.db`): 줄마다 `tenant_id` 칸. 옛 DB는 켤 때 칸을 붙이고 기존 줄은 `catbooks`로.
  새 대화는 요청 메타의 회사(별칭은 정식 id로), 없거나 맞지 않으면 캣북스
- 배포 기록(`INFRA_DEPLOYMENTS_FILE`): 정했으면 그 경로. 안 정했고 회사 폴더를 켰으면
  `<회사 폴더>/catbooks/infra_deployments.json`(있을 때만, 옛 자리에 있으면 옮긴다).
  둘 다 없으면 패키지 안의 시연 이력(지금 동작)
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ax_agent_template import companies, company_files
from ax_contracts.schemas import AgentRequest, RequestMeta
from infra_agent import deployments
from infra_agent.chat import a2a, threads
from infra_agent.chat.turn import ChatOut, ChatService, set_chat_service


@pytest.fixture(autouse=True)
def _reset_service():
    yield
    set_chat_service(None)


def _tenants(path: Path) -> list[tuple[str, str]]:
    db = sqlite3.connect(path)
    try:
        return db.execute("SELECT id, tenant_id FROM infra_chat_threads ORDER BY seq").fetchall()
    finally:
        db.close()


def test_old_db_gets_a_company_column_and_old_rows_are_catbooks(tmp_path: Path) -> None:
    path = tmp_path / "infra_chat.db"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE infra_chat_threads (id TEXT PRIMARY KEY, seq INTEGER NOT NULL,"
        " title TEXT NOT NULL, status TEXT NOT NULL, who TEXT, summary TEXT NOT NULL DEFAULT '',"
        " messages TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    db.execute(
        "INSERT INTO infra_chat_threads VALUES"
        " ('infrachat-1', 1, '예전 대화', 'closed', 'yejin', '', '[]', 'x', 'x')"
    )
    db.commit()
    db.close()

    store = threads.ChatStore(str(path))
    assert _tenants(path) == [("infrachat-1", "catbooks")]
    assert store.get("infrachat-1")["title"] == "예전 대화"  # 기존 줄은 그대로 보인다
    threads.ChatStore(str(path))  # 두 번 열어도 칸을 다시 붙이지 않는다
    assert _tenants(path) == [("infrachat-1", "catbooks")]


def test_new_threads_are_catbooks_unless_told(tmp_path: Path) -> None:
    path = tmp_path / "infra_chat.db"
    store = threads.ChatStore(str(path))
    store.create("하나", "yejin")
    store.create("둘", "yejin", tenant_id="shop-demo")  # 별칭은 정식 id로
    store.create("셋", "yejin", tenant_id="nubi-tour")
    assert [t for _, t in _tenants(path)] == ["catbooks", "catbooks", "nubi-tour"]


def test_a_bad_company_id_is_stored_as_catbooks(tmp_path: Path) -> None:
    path = tmp_path / "infra_chat.db"
    store = threads.ChatStore(str(path))
    store.create("하나", "yejin", tenant_id="Not OK!")
    assert _tenants(path)[0][1] == "catbooks"


class FakeLLM:
    async def structured(self, system, schema, *, untrusted):  # noqa: ANN001, ANN201
        return ChatOut(reply="네", summary="요약")


def test_a2a_send_stores_the_request_company(tmp_path: Path) -> None:
    path = tmp_path / "infra_chat.db"
    store = threads.ChatStore(str(path))
    svc = ChatService(lambda: store, lambda: FakeLLM())
    set_chat_service(svc)

    def req(tenant: str | None) -> AgentRequest:
        extra = {} if tenant is None else {"tenant_id": tenant}
        meta = RequestMeta(
            context_id="ctx-t",
            token="t",
            deadline=datetime.now(UTC) + timedelta(seconds=30),
            idempotency_key="k-1",
            **extra,
        )
        return AgentRequest(meta=meta, skill="infra_chat_send", input={"who": "y", "message": "hi"})

    async def run() -> None:
        await a2a.infra_chat_send(req(None))  # 기본값 shop-demo → catbooks
        await a2a.infra_chat_send(req("studyon-edu"))
        await svc.wait_idle()

    asyncio.run(run())
    assert [t for _, t in _tenants(path)] == ["catbooks", "studyon-edu"]


# ---------------------------------------------------------------- 배포 기록 파일
ROWS = {
    "deployments": [
        {
            "deploy_id": "d-company",
            "service": "shop-api",
            "version": "v9.9.9",
            "deployed_at": "2026-11-20T00:00:00Z",
        }
    ]
}


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / ".data"
    root.mkdir()
    monkeypatch.delenv("INFRA_DEPLOYMENTS_FILE", raising=False)
    monkeypatch.setenv(companies.COMPANIES_DIR_ENV, str(root / "companies"))
    monkeypatch.delenv(company_files.FOLDERS_ENV, raising=False)
    company_files.reset_for_tests()
    return root


def _ids() -> list[str]:
    return [r["deploy_id"] for r in deployments.load_rows()]


def test_without_any_file_reads_the_package_history(data: Path) -> None:
    assert _ids() and "d-company" not in _ids()
    assert not (data / "companies").exists()  # 회사 폴더를 만들지 않는다


def test_without_company_folders_reads_the_package_history(data: Path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv(companies.COMPANIES_DIR_ENV, raising=False)
    (data / "infra_deployments.json").write_text(json.dumps(ROWS), encoding="utf-8")
    assert "d-company" not in _ids()


def test_the_company_folder_file_is_read(data: Path) -> None:
    folder = data / "companies" / "catbooks"
    folder.mkdir(parents=True)
    (folder / "infra_deployments.json").write_text(json.dumps(ROWS), encoding="utf-8")
    assert _ids() == ["d-company"]


def test_an_old_file_moves_to_the_company_folder(data: Path) -> None:
    (data / "infra_deployments.json").write_text(json.dumps(ROWS), encoding="utf-8")
    assert _ids() == ["d-company"]
    assert (data / "companies" / "catbooks" / "infra_deployments.json").exists()
    assert (data / "infra_deployments.json.moved").exists()


def test_the_env_file_wins(data: Path, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    pinned = tmp_path / "pinned.json"
    pinned.write_text(json.dumps(ROWS), encoding="utf-8")
    monkeypatch.setenv("INFRA_DEPLOYMENTS_FILE", str(pinned))
    folder = data / "companies" / "catbooks"
    folder.mkdir(parents=True)
    (folder / "infra_deployments.json").write_text('{"deployments": []}', encoding="utf-8")
    assert _ids() == ["d-company"]
