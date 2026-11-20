import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import BaseModel

from ax_agent_template import llm_usage
from ax_agent_template.llm import LLMClient, LLMResponseError, LLMSettings, MockLLMClient
from ax_agent_template.llm_usage import SqliteUsageStore
from ax_contracts.data_access.llm_usage import LlmCallRecord, LlmUsageQuery


@pytest.fixture
def store() -> Iterator[SqliteUsageStore]:
    s = SqliteUsageStore(":memory:")
    llm_usage.set_store(s)
    yield s
    llm_usage.reset_store()
    s.close()


def _all(store: SqliteUsageStore, **query):
    return store.query(LlmUsageQuery.model_validate(query)).records


@pytest.mark.asyncio
async def test_mock_embed_records_usage_with_zero_tokens(store: SqliteUsageStore) -> None:
    await MockLLMClient(dept="memory").embed(["고객 원문"], context_id="ctx-1")
    [record] = _all(store)
    assert record.context_id == "ctx-1"
    assert record.tenant_id == "shop-demo"
    assert (record.source, record.department, record.model) == ("memory", "memory", "mock")
    assert (record.input_tokens, record.output_tokens, record.success) == (0, 0, True)
    assert "고객 원문" not in json.dumps(record.model_dump(mode="json"), ensure_ascii=False)


@pytest.mark.asyncio
async def test_real_embed_records_token_counts_per_context(
    monkeypatch: pytest.MonkeyPatch, store: SqliteUsageStore
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [{"index": 0, "embedding": [1.0]}],
                "usage": {"prompt_tokens": 7, "total_tokens": 7},
            },
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler))
    )
    client = LLMClient("memory", LLMSettings(base_url="http://llm/v1", embed_model="m"))
    await client.embed(["a"], context_id="ctx-2")
    await client.embed(["b"], context_id="ctx-2")
    records = _all(store, context_ids=["ctx-2"])
    assert len(records) == 2
    assert sum(r.input_tokens for r in records) == 14
    assert {r.context_id for r in records} == {"ctx-2"}


@pytest.mark.asyncio
async def test_failed_real_embed_records_failure(
    monkeypatch: pytest.MonkeyPatch, store: SqliteUsageStore
) -> None:
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
    )
    client = LLMClient("memory", LLMSettings(base_url="http://llm/v1", embed_model="m"))
    with pytest.raises(httpx.HTTPStatusError):
        await client.embed(["a"], context_id="ctx-3")
    [record] = _all(store)
    assert record.success is False


def test_query_filters_by_department_context_and_time(store: SqliteUsageStore) -> None:
    for dept, ctx in [("hr", "ctx-a"), ("finance", "ctx-a"), ("hr", None)]:
        llm_usage.record_call(
            source=dept,
            department=dept,
            model="m",
            input_tokens=1,
            output_tokens=2,
            success=True,
            context_id=ctx,
        )
    assert {r.department for r in _all(store, context_ids=["ctx-a"])} == {"hr", "finance"}
    assert len(_all(store, departments=["hr"])) == 2
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    assert _all(store, **{"from": future}) == []
    limited = store.query(LlmUsageQuery.model_validate({"limit": 2}))
    assert len(limited.records) == 2 and limited.truncated is True


def test_without_a_store_nothing_is_written_and_nothing_breaks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm_usage.reset_store()
    monkeypatch.delenv(llm_usage.DB_ENV, raising=False)
    assert llm_usage.get_store() is None
    llm_usage.record_call(
        source="hr", department="hr", model=None, input_tokens=0, output_tokens=0, success=True
    )


def test_a_failing_store_does_not_stop_the_caller() -> None:
    class Broken:
        def write(self, record) -> None:
            raise RuntimeError("disk full")

    llm_usage.set_store(Broken())  # type: ignore[arg-type]
    try:
        llm_usage.record_call(
            source="hr", department="hr", model="m", input_tokens=1, output_tokens=1, success=True
        )
    finally:
        llm_usage.reset_store()


def test_file_store_is_shared_between_connections(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = str(tmp_path / "usage.db")
    monkeypatch.setenv(llm_usage.DB_ENV, path)
    llm_usage.reset_store()
    llm_usage.record_call(
        source="hr", department="hr", model="m", input_tokens=3, output_tokens=4, success=True
    )
    reader = SqliteUsageStore(path)
    [record] = reader.query(LlmUsageQuery()).records
    assert (record.input_tokens, record.output_tokens) == (3, 4)
    reader.close()
    llm_usage.reset_store()


class _Answer(BaseModel):
    value: int


def _structured_client(handler) -> LLMClient:
    settings = LLMSettings(base_url="http://llm/v1", model="m")
    return LLMClient("memory", settings, transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_structured_success_records_token_counts(store: SqliteUsageStore) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"value": 1}'}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 4},
            },
        )

    await _structured_client(handler).structured("질문", _Answer, context_id="ctx-s")
    [record] = _all(store, context_ids=["ctx-s"])
    assert (record.input_tokens, record.output_tokens, record.success) == (11, 4, True)
    assert record.model == "m"


@pytest.mark.asyncio
async def test_structured_failures_record_each_request(
    monkeypatch: pytest.MonkeyPatch, store: SqliteUsageStore
) -> None:
    monkeypatch.setenv("MEMORY_LLM_MAX_RETRIES", "1")

    def bad_schema(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "not json"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            },
        )

    with pytest.raises(LLMResponseError):
        await _structured_client(bad_schema).structured("질문", _Answer, context_id="ctx-f")
    records = _all(store, context_ids=["ctx-f"])
    assert [r.success for r in records] == [False, False]
    assert sum(r.input_tokens for r in records) == 10

    with pytest.raises(LLMResponseError):
        await _structured_client(lambda r: httpx.Response(500)).structured(
            "질문", _Answer, context_id="ctx-h"
        )
    [http_record] = _all(store, context_ids=["ctx-h"])
    assert (http_record.input_tokens, http_record.success) == (0, False)

    # HTTP 오류는 상태 코드를 담는다(호출하는 쪽이 429·503만 골라 다시 시도할 수 있게)
    with pytest.raises(LLMResponseError) as limited:
        await _structured_client(lambda r: httpx.Response(429)).structured(
            "질문", _Answer, context_id="ctx-i"
        )
    assert limited.value.status_code == 429


def _rec(log_id: str, account_id: str | None = None) -> LlmCallRecord:
    return LlmCallRecord(
        log_id=log_id,
        tenant_id="catbooks",
        occurred_at=datetime.now(UTC),
        source="planning",
        department="planning",
        model="m",
        input_tokens=3,
        output_tokens=4,
        success=True,
        account_id=account_id,
    )


def test_account_id_is_stored_and_can_be_filtered(store: SqliteUsageStore) -> None:
    """개인 MCP 연결 호출의 계정 칸(11/20). 계정을 주면 그 계정 호출만, 안 주면 모두."""
    store.write(_rec("a"))
    store.write(_rec("b", "acc-1"))
    store.write(_rec("c", "acc-2"))
    every = _all(store, tenant_id="catbooks")
    assert [(r.log_id, r.account_id) for r in every] == [
        ("a", None), ("b", "acc-1"), ("c", "acc-2"),
    ]
    mine = _all(store, tenant_id="catbooks", account_id="acc-1")
    assert [r.log_id for r in mine] == ["b"]


def test_old_db_without_account_column_gets_it_once(tmp_path) -> None:
    """옛 DB(계정 칸 없음)는 열 때 칸을 한 번 더한다. 옛 기록은 None이라 회사 합계 그대로."""
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE llm_calls (seq INTEGER PRIMARY KEY AUTOINCREMENT,"
        " log_id TEXT NOT NULL UNIQUE, tenant_id TEXT NOT NULL, context_id TEXT,"
        " occurred_at TEXT NOT NULL, source TEXT NOT NULL, department TEXT NOT NULL,"
        " model TEXT, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,"
        " success INTEGER NOT NULL);"
        "INSERT INTO llm_calls (log_id, tenant_id, occurred_at, source, department, model,"
        " input_tokens, output_tokens, success) VALUES"
        " ('old', 'catbooks', '2026-11-01T00:00:00.000000Z', 'data', 'data', 'm', 1, 2, 1);"
    )
    conn.commit()
    conn.close()
    for _ in range(2):  # 두 번 열어도 칸이 한 번만 생긴다
        s = SqliteUsageStore(path)
        s.close()
    s = SqliteUsageStore(path)
    s.write(_rec("new", "acc-1"))
    rows = s.query(LlmUsageQuery(tenant_id="catbooks")).records
    assert [(r.log_id, r.account_id) for r in rows] == [("old", None), ("new", "acc-1")]
    s.close()


def test_record_call_takes_an_account_id(store: SqliteUsageStore) -> None:
    llm_usage.record_call(
        source="dev", department="dev", model="m", input_tokens=1, output_tokens=1,
        success=True, tenant_id="catbooks", account_id="acc-1",
    )
    [record] = _all(store, tenant_id="catbooks")
    assert record.account_id == "acc-1"


def test_account_scope_fills_the_account_of_calls_inside_it(store: SqliteUsageStore) -> None:
    """개인 MCP 연결로 시킨 일 안의 호출은 그 사람 계정으로 남는다. 밖은 None(11/20)."""
    with llm_usage.account_scope("acc-7"):
        llm_usage.record_call(
            source="data", department="data", model="m", input_tokens=2, output_tokens=3,
            success=True, tenant_id="catbooks",
        )
    llm_usage.record_call(
        source="data", department="data", model="m", input_tokens=1, output_tokens=1,
        success=True, tenant_id="catbooks",
    )
    with llm_usage.account_scope(None):
        llm_usage.record_call(
            source="data", department="data", model="m", input_tokens=1, output_tokens=1,
            success=True, tenant_id="catbooks",
        )
    rows = _all(store, tenant_id="catbooks")
    assert [r.account_id for r in rows] == ["acc-7", None, None]


@pytest.mark.asyncio
async def test_a2a_request_on_behalf_of_names_the_account_of_its_llm_calls(
    store: SqliteUsageStore,
) -> None:
    """받은 A2A 메타의 on_behalf_of가 그 요청을 처리하며 부른 LLM 호출의 계정 칸이 된다."""
    from ax_agent_template.server import create_agent_app
    from ax_contracts.schemas import AgentCard, AgentRequest, AgentResult, TaskStatus

    card = AgentCard.model_validate(
        {
            "name": "usage_agent", "owner": "test", "description": "테스트용",
            "url": "http://usage_agent:9999/a2a",
            "skills": [{
                "id": "talk", "description": "talk 테스트 skill. 상태를 바꾸지 않는다.",
                "input_schema": "x", "output_schema": "x", "grade": "read",
                "allowed_requesters": ["orchestrator"], "priority": "optional",
            }],
        }
    )

    async def talk(request: AgentRequest) -> AgentResult:
        await MockLLMClient(dept="usage").embed(["x"], context_id=request.meta.context_id)
        return AgentResult(meta=request.meta, status=TaskStatus.COMPLETED, output={})

    import httpx

    app = create_agent_app(card, {"talk": talk})
    deadline = (datetime.now(UTC) + timedelta(seconds=60)).isoformat()
    for person in ("acc-9", None):
        meta: dict = {"context_id": "ctx-u", "token": "mock:orchestrator", "deadline": deadline}
        if person:
            meta["on_behalf_of"] = person
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://u") as http:
            res = await http.post("/a2a", json={"meta": meta, "skill": "talk", "input": {}})
        assert res.status_code == 200 and res.json()["status"] == "completed", res.text
    assert [r.account_id for r in _all(store)] == ["acc-9", None]
