"""인프라 AI 직접 대화: 저장 후 answering → 뒤에서 답, 끝내기·30분 자동 끝내기, 열람 제한,
읽기 전용 인프라 사실(배포·쇼핑몰 상태·진단·총괄 사건), LLM 없을 때, A2A skill(`infra_chat_*`)."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from infra_agent.chat import a2a, facts, threads
from infra_agent.chat.turn import ChatOut, ChatRefused, ChatService, set_chat_service
from infra_agent.main import app, load_card


@pytest.fixture(autouse=True)
def _reset_service():
    yield
    set_chat_service(None)


class FakeLLM:
    def __init__(self, error: Exception | None = None) -> None:
        self.seen: list[dict] = []
        self.prompts: list[str] = []
        self.error = error

    async def structured(self, system, schema, *, untrusted):
        self.prompts.append(system)
        self.seen.append(untrusted)
        if self.error:
            raise self.error
        return ChatOut(reply="최근 배포는 payment-api v2.4.1이에요.", summary="최근 배포 질문")


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 9, 6, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def _service(llm, clock: Clock | None = None) -> ChatService:
    store = threads.ChatStore(clock=clock or Clock())
    svc = ChatService(lambda: store, lambda: llm)
    set_chat_service(svc)
    return svc


def _req(skill: str, data: dict) -> AgentRequest:
    deadline = datetime.now(UTC) + timedelta(seconds=30)
    meta = RequestMeta(context_id="ctx-t", token="t", deadline=deadline, idempotency_key="k-1")
    return AgentRequest(meta=meta, skill=skill, input=data)


def test_send_returns_answering_then_answer_arrives_with_infra_facts() -> None:
    from ax_agent_template import company_scope

    async def run() -> None:
        llm = FakeLLM()
        svc = _service(llm)
        with company_scope.using("catbooks"):  # 요청 회사 칸의 배포만 사실에 넣는다
            th = svc.send("yejin", None, "최근 배포 알려 줘")
        assert th["id"] == "infrachat-1" and th["status"] == "answering"
        await svc.wait_idle()
        done = svc.get("yejin", th["id"])
        assert done["status"] == "talking" and done["summary"] == "최근 배포 질문"
        got = llm.seen[0]["facts"]
        # 숫자·상태는 코드가 모은 사실에만 있다 (배포 기록, 쇼핑몰 결제 서버 상태)
        assert got["recent_deployments"] and "version" in got["recent_deployments"][0]
        assert got["shop_payment_server"]["failing"] is True
        assert "recent_incidents" in got  # 목 모드는 총괄을 부르지 않고 빈 목록
        # 대화로는 롤백하지 않는다고 프롬프트에 박혀 있다
        assert "롤백" in llm.prompts[0] and "조회와 설명만" in llm.prompts[0]

    asyncio.run(run())


def test_busy_closed_and_other_people() -> None:
    async def run() -> None:
        svc = _service(FakeLLM())
        tid = svc.send("yejin", None, "안녕")["id"]
        with pytest.raises(ChatRefused) as busy:
            svc.send("yejin", tid, "또")
        assert busy.value.outcome == "thread_busy"
        await svc.wait_idle()
        seen = svc.get("taeyoon", tid)
        assert seen["restricted"] and seen["messages"] == []
        with pytest.raises(ChatRefused) as other:
            svc.send("taeyoon", tid, "끼어들기")
        assert other.value.status == 403
        assert svc.close("yejin", tid)["status"] == "closed"
        with pytest.raises(ChatRefused) as closed:
            svc.send("yejin", tid, "다시")
        assert closed.value.outcome == "thread_closed"

    asyncio.run(run())


def test_remove_own_thread_then_get_is_not_found() -> None:
    async def run() -> None:
        svc = _service(FakeLLM())
        tid = svc.send("yejin", None, "안녕")["id"]
        await svc.wait_idle()
        ref = {"who": "yejin", "thread_id": tid}
        done = await a2a.infra_chat_remove(_req("infra_chat_remove", ref))
        assert done.status == TaskStatus.COMPLETED
        assert done.output == {"thread_id": tid, "removed": True}
        gone = await a2a.infra_chat_get(_req("infra_chat_get", ref))
        assert gone.status == TaskStatus.FAILED and gone.outcome == "not_found"
        assert svc.list("yejin") == []

    asyncio.run(run())


def test_removed_thread_is_hidden_not_deleted_and_late_answer_does_not_revive_it() -> None:
    async def run() -> None:
        svc = _service(FakeLLM())
        tid = svc.send("yejin", None, "안녕")["id"]  # 아직 answering
        assert svc.remove("yejin", tid) == tid
        await svc.wait_idle()  # 뒤늦은 답이 붙어도 되살리지 않는다
        with pytest.raises(ChatRefused) as gone:
            svc.get("yejin", tid)
        assert gone.value.outcome == "not_found"
        assert svc.list("yejin") == []
        rows = svc.store._db.execute(
            "SELECT removed_at, removed_by FROM infra_chat_threads WHERE id = ?", (tid,)
        ).fetchall()
        assert len(rows) == 1  # 행은 남는다
        assert rows[0]["removed_at"] and rows[0]["removed_by"] == "yejin"
        # 번호는 이어진다(MAX(seq)가 그대로)
        assert svc.store.create("다음", "yejin")["id"] == "infrachat-2"

    asyncio.run(run())


def test_removing_twice_is_not_found_and_keeps_first_removal_record() -> None:
    svc = _service(FakeLLM())
    tid = svc.store.create("제목", "yejin")["id"]
    svc.remove("yejin", tid)
    sql = "SELECT removed_at, removed_by FROM infra_chat_threads WHERE id = ?"
    first = tuple(svc.store._db.execute(sql, (tid,)).fetchone())
    with pytest.raises(ChatRefused) as again:
        svc.remove("yejin", tid)
    assert again.value.outcome == "not_found"
    assert tuple(svc.store._db.execute(sql, (tid,)).fetchone()) == first


def test_old_db_without_removed_columns_is_migrated_on_open(tmp_path) -> None:
    path = str(tmp_path / "infra_chat.db")
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE infra_chat_threads (id TEXT PRIMARY KEY, seq INTEGER NOT NULL,"
        " title TEXT NOT NULL, status TEXT NOT NULL, who TEXT,"
        " summary TEXT NOT NULL DEFAULT '', messages TEXT NOT NULL,"
        " created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    old.execute(
        "INSERT INTO infra_chat_threads VALUES"
        " ('infrachat-1', 1, '옛 대화', 'talking', 'yejin', '', '[]', 't', 't')"
    )
    old.commit()
    old.close()
    threads.ChatStore(path)  # 한 번 열어 열을 붙이고
    store = threads.ChatStore(path)  # 다시 열어도 탈 없다(멱등)
    assert [t["id"] for t in store.list_all()] == ["infrachat-1"]
    assert store.hide("infrachat-1", "yejin") is True
    assert store.list_all() == [] and store.get("infrachat-1") is None


def test_remove_others_thread_is_forbidden_and_unknown_is_not_found() -> None:
    async def run() -> None:
        svc = _service(FakeLLM())
        tid = svc.send("yejin", None, "안녕")["id"]
        await svc.wait_idle()
        other = await a2a.infra_chat_remove(
            _req("infra_chat_remove", {"who": "reviewer", "thread_id": tid})
        )
        assert other.status == TaskStatus.FAILED and other.outcome == "forbidden"
        assert svc.get("yejin", tid)["id"] == tid  # 남은 그대로
        unknown = await a2a.infra_chat_remove(
            _req("infra_chat_remove", {"who": "yejin", "thread_id": "nope"})
        )
        assert unknown.status == TaskStatus.FAILED and unknown.outcome == "not_found"

    asyncio.run(run())


def test_remove_while_answering_does_not_resurrect() -> None:
    async def run() -> None:
        svc = _service(FakeLLM())
        tid = svc.send("yejin", None, "안녕")["id"]  # 아직 answering
        assert svc.remove("yejin", tid) == tid
        await svc.wait_idle()  # 답이 뒤늦게 끝나도 되살아나지 않는다
        with pytest.raises(ChatRefused) as gone:
            svc.get("yejin", tid)
        assert gone.value.outcome == "not_found"

    asyncio.run(run())


def test_llm_failure_says_why_and_idle_threads_close() -> None:
    async def run() -> None:
        clock = Clock()
        svc = _service(FakeLLM(RuntimeError("LLM API가 429를 돌려줬다")), clock)
        tid = svc.send("yejin", None, "안녕")["id"]
        await svc.wait_idle()
        last = svc.get("yejin", tid)["messages"][-1]
        assert last["kind"] == "error" and "429" in last["text"]
        clock.now += timedelta(minutes=31)
        assert svc.get("yejin", tid)["status"] == "closed"

    asyncio.run(run())


def test_without_llm_is_refused() -> None:
    svc = _service(None)
    with pytest.raises(ChatRefused) as exc:
        svc.send("yejin", None, "안녕")
    assert exc.value.code == "LLM_NOT_CONFIGURED"


def test_facts_keep_going_when_one_part_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom() -> dict:
        raise RuntimeError("shop down")

    monkeypatch.setattr(facts, "_shop", boom)
    from ax_agent_template import company_scope

    with company_scope.using("catbooks"):
        got = asyncio.run(facts.collect())
    assert "shop_payment_server" in got["unavailable"] and "shop_payment_server" not in got
    assert got["recent_deployments"]


def test_a2a_skills_use_the_same_service_and_are_on_the_card() -> None:
    async def run() -> None:
        svc = _service(FakeLLM())
        sent = await a2a.infra_chat_send(
            _req("infra_chat_send", {"who": "yejin", "message": "안녕"})
        )
        assert sent.status == TaskStatus.COMPLETED
        await svc.wait_idle()
        tid = sent.output["thread"]["id"]
        listed = await a2a.infra_chat_list(_req("infra_chat_list", {"who": "taeyoon"}))
        assert listed.output["threads"][0]["restricted"]
        missing = await a2a.infra_chat_get(
            _req("infra_chat_get", {"who": "yejin", "thread_id": "x"})
        )
        assert missing.status == TaskStatus.FAILED and missing.outcome == "not_found"
        closed = await a2a.infra_chat_close(
            _req("infra_chat_close", {"who": "yejin", "thread_id": tid})
        )
        assert closed.output["thread"]["status"] == "closed"
        bad = await a2a.infra_chat_send(_req("infra_chat_send", {"who": "yejin", "message": ""}))
        assert bad.status == TaskStatus.FAILED and bad.outcome == "invalid_input"

    asyncio.run(run())
    skills = {s.id: s for s in load_card().skills}
    names = ("infra_chat_send", "infra_chat_close", "infra_chat_list", "infra_chat_get")
    for name in names:
        assert list(skills[name].allowed_requesters) == ["dashboard-api"]
    assert (skills["infra_chat_send"].grade, skills["infra_chat_get"].grade) == ("A", "read")


def test_other_agents_cannot_open_the_chat() -> None:
    body = {
        "meta": {
            "context_id": "ctx-t",
            "token": "mock:marketing",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": "infra_chat_list",
        "input": {"who": "yejin"},
    }
    res = TestClient(app).post("/a2a", json=body).json()
    assert res["status"] != "completed"


def test_chat_threads_are_seen_only_by_their_company() -> None:
    """누비투어 요청에는 캣북스 대화가 안 보인다(목록·열기·숨기기).
    회사가 안 걸린 요청은 캣북스만, 요청 밖 정리 루틴은 모두 본다 (11/25·11/27)."""
    from ax_agent_template import company_scope

    store = threads.ChatStore()
    cat = store.create("캣북스 질문", "u1", "catbooks")
    nubi = store.create("누비 질문", "u2", "nubi-tour")
    with company_scope.using("nubi-tour"):
        assert [t["id"] for t in store.list_all()] == [nubi["id"]]
        assert store.get(cat["id"]) is None
        assert store.hide(cat["id"], "u2") is False
        assert store.begin_turn(cat["id"], "몰래")[0] == "missing"
    with company_scope.using("catbooks"):
        assert [t["id"] for t in store.list_all()] == [cat["id"]]
        assert store.get(nubi["id"]) is None
    # 요청 밖(회사 없음)은 캣북스만(11/27: 예전에는 전부). 정리 루틴은 모든 회사를 본다
    assert [t["id"] for t in store.list_all()] == [cat["id"]]
    assert set(store.expire_idle(minutes=-1)) == {cat["id"], nubi["id"]}


# --- 사람이 확정한 원인 (dashboard-api 읽기 창구) ---

_REAL_CLIENT = httpx.AsyncClient


def _problems(*ids) -> dict:
    return {
        "configured": True,
        "count": len(ids),
        "problems": [{"incident_id": i, "title": f"장애 {i}", "component": "payment"} for i in ids],
    }


def _wire(monkeypatch: pytest.MonkeyPatch, store: dict, problems: dict, seen: list) -> None:
    """회사별 확정 원인 저장소를 흉내 낸 dashboard-api. `store[회사][번호] = 확정 원인 줄`."""
    monkeypatch.setenv("INFRA_TOKEN", "tok-infra")
    monkeypatch.setattr(facts, "is_mock", lambda *_a, **_k: False)

    async def fake_dejafix() -> dict:
        return problems

    async def no_incidents() -> list:
        return []

    monkeypatch.setattr(facts, "_dejafix", fake_dejafix)
    monkeypatch.setattr(facts, "_incidents", no_incidents)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.url.params), dict(request.headers)))
        company = request.headers.get("X-AX-Company", "")
        ids = [i for i in request.url.params["ids"].split(",")]
        rows = {i: store.get(company, {}).get(int(i)) for i in ids}
        return httpx.Response(200, json={"data": {k: v for k, v in rows.items() if v}})

    monkeypatch.setattr(
        facts.httpx,
        "AsyncClient",
        lambda **kw: _REAL_CLIENT(transport=httpx.MockTransport(handler), **kw),
    )


ROW = {
    "text": "결제 서버 메모리 누수",
    "saved_by": "재현",
    "saved_at": "2026-11-26T01:00:00+00:00",
    "from_rank": 2,
}


def test_facts_attach_human_confirmed_cause_and_null_when_none(monkeypatch) -> None:
    from ax_agent_template import company_scope

    seen: list = []
    store = {"catbooks": {7: ROW}, "nubi-tour": {7: {**ROW, "text": "다른 회사 원인"}, 8: ROW}}
    _wire(monkeypatch, store, _problems(7, 9), seen)
    with company_scope.using("catbooks"):
        got = asyncio.run(facts.collect())
    by_id = {p["incident_id"]: p for p in got["dejafix_problems"]["problems"]}
    assert by_id[7]["confirmed_cause"] == ROW  # 사람이 확정한 원인 그대로
    assert by_id[9]["confirmed_cause"] is None  # 없으면 null, 지어내지 않는다
    assert "confirmed_causes" not in got["unavailable"]
    path, params, headers = seen[0]
    assert path == "/api/dashboard/incident-causes" and params == {"ids": "7,9"}
    assert headers["x-ax-company"] == "catbooks" and headers["authorization"] == "Bearer tok-infra"
    assert "다른 회사" not in str(got)  # 다른 회사 것이 안 섞인다


def test_facts_confirmed_cause_is_scoped_to_the_requesting_company(monkeypatch) -> None:
    from ax_agent_template import company_scope

    store = {"catbooks": {7: ROW}, "nubi-tour": {7: {**ROW, "text": "누비 원인"}}}
    _wire(monkeypatch, store, _problems(7), [])
    with company_scope.using("nubi-tour"):
        got = asyncio.run(facts.collect())
    assert got["dejafix_problems"]["problems"][0]["confirmed_cause"]["text"] == "누비 원인"


def test_facts_confirmed_cause_text_is_capped(monkeypatch) -> None:
    from ax_agent_template import company_scope

    _wire(monkeypatch, {"catbooks": {7: {**ROW, "text": "가" * 2000}}}, _problems(7), [])
    with company_scope.using("catbooks"):
        got = asyncio.run(facts.collect())
    assert len(got["dejafix_problems"]["problems"][0]["confirmed_cause"]["text"]) == (
        facts.CAUSE_CHARS
    )


def test_facts_keep_going_when_confirmed_causes_cannot_be_read(monkeypatch) -> None:
    from ax_agent_template import company_scope

    seen: list = []
    _wire(monkeypatch, {}, _problems(7), seen)

    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"code": "X"}})

    monkeypatch.setattr(
        facts.httpx,
        "AsyncClient",
        lambda **kw: _REAL_CLIENT(transport=httpx.MockTransport(down), **kw),
    )
    with company_scope.using("catbooks"):
        got = asyncio.run(facts.collect())
    assert "confirmed_causes" in got["unavailable"]
    assert "confirmed_cause" not in got["dejafix_problems"]["problems"][0]  # 없다고 하지 않는다
    assert got["recent_deployments"] and got["dejafix_problems"]["problems"][0]["incident_id"] == 7


def test_facts_without_company_or_problems_do_not_call_dashboard(monkeypatch) -> None:
    from ax_agent_template import company_scope

    seen: list = []
    _wire(monkeypatch, {"catbooks": {7: ROW}}, _problems(), seen)  # 장애 없음
    with company_scope.using("catbooks"):
        got = asyncio.run(facts.collect())
    assert seen == [] and "confirmed_causes" not in got["unavailable"]

    _wire(
        monkeypatch, {"catbooks": {7: ROW}}, _problems(7), seen
    )  # 회사 없음: 읽지 못함으로 남긴다
    token = company_scope._ACTIVE.set(None)
    try:
        got = asyncio.run(facts.collect())
    finally:
        company_scope._ACTIVE.reset(token)
    assert seen == [] and "confirmed_causes" in got["unavailable"]
    assert "confirmed_cause" not in got["dejafix_problems"]["problems"][0]


def test_prompt_separates_human_confirmed_cause_from_ai_candidates() -> None:
    from infra_agent.chat.turn import SYSTEM_PROMPT

    assert "confirmed_cause" in SYSTEM_PROMPT and "사람이 확정한 원인" in SYSTEM_PROMPT
    assert "섞지 말고" in SYSTEM_PROMPT and "지어내지 않는다" in SYSTEM_PROMPT


def _diag(minutes: int = 0):
    from ax_contracts.schemas import DiagnoseIncidentOutput
    from infra_agent.diagnosis import Diagnosis

    return Diagnosis(
        output=DiagnoseIncidentOutput(status="undetermined", suspects=[]),
        candidates=(),
        diagnosed_at=datetime(2026, 11, 9, 6, minutes, tzinfo=UTC),
        window_from=None,
    )


def test_facts_diagnoses_are_only_the_requesting_companys() -> None:
    from ax_agent_template import company_scope
    from infra_agent.runtime import get_runtime

    rt = get_runtime()
    with company_scope.using("nubi-tour"):
        rt.save_diagnosis("ctx-nubi", _diag(1))
    with company_scope.using("catbooks"):
        rt.save_diagnosis("ctx-cat", _diag(2))
    with company_scope.using("catbooks"):
        got = asyncio.run(facts.collect())
    assert [d["context_id"] for d in got["recent_diagnoses"]] == ["ctx-cat"]
    with company_scope.using("nubi-tour"):
        got = asyncio.run(facts.collect())
    assert [d["context_id"] for d in got["recent_diagnoses"]] == ["ctx-nubi"]


def test_facts_without_company_see_no_diagnoses_and_no_catbooks_deployments() -> None:
    from ax_agent_template import company_scope
    from infra_agent.runtime import get_runtime

    with company_scope.using("catbooks"):
        get_runtime().save_diagnosis("ctx-cat", _diag(2))
        assert asyncio.run(facts.collect())["recent_deployments"]
    token = company_scope._ACTIVE.set(None)
    try:
        got = asyncio.run(facts.collect())
    finally:
        company_scope._ACTIVE.reset(token)
    assert got["recent_diagnoses"] == [] and got["recent_deployments"] == []
