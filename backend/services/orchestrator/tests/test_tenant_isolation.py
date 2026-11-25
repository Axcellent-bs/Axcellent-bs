"""총괄은 회사마다 이벤트 목록·저장소·대화를 따로 둔다 (여러 회사 5단계 = {회사: manager}, 11/23).

A 회사 요청·이벤트·로그인은 B 회사 기록을 보지 못한다(없는 것처럼). 요청 밖은 캣북스 그대로.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_scope, human_auth
from ax_agent_template.config import set_agent_name
from ax_contracts.schemas import AgentRequest, AgentResult, EventEnvelope, EventType, RequestMeta
from ax_orchestrator.chat import threads
from ax_orchestrator.main import app, state

SECRET = "x" * 40
AUTH = {"Authorization": "Bearer admin-test-token"}


@pytest.fixture(autouse=True)
def fresh(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """회사마다 매니저가 없는 새 상태. 저장 파일은 시험마다 따로, 부서는 가짜."""
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("ORCHESTRATOR_DB", str(tmp_path / "orchestrator.db"))
    monkeypatch.setenv("ORCHESTRATOR_CHAT_DB", str(tmp_path / "chat.db"))
    monkeypatch.setenv(human_auth.SESSION_SECRET_ENV, SECRET)
    monkeypatch.setattr(state, "mock", True)
    monkeypatch.setattr(state, "latency", 0.0)
    monkeypatch.setattr(state, "managers", {})
    monkeypatch.setattr(state, "stores", {})
    monkeypatch.setattr(state, "watchers", {})
    monkeypatch.setattr(threads, "_store", None)
    monkeypatch.setattr(threads, "_company_stores", {})
    yield
    for store in state.stores.values():
        store.close()
    set_agent_name("orchestrator")


def _event(event_id: str, tenant: str | None = None) -> EventEnvelope:
    body: dict = {
        "event_id": event_id,
        "type": EventType.SHOP_DEGRADED,
        "occurred_at": datetime.now(UTC),
        "source": "shop-api",
        "idempotency_key": event_id,
        "data": {"component": "payment"},
    }
    if tenant is not None:
        body["tenant_id"] = tenant
    return EventEnvelope(**body)


async def _open(company: str | None, event_id: str, wait: bool = True) -> str:
    """그 회사 매니저로 이벤트를 하나 연다. 열린 이벤트의 번호를 돌려준다."""
    with company_scope.using(company):
        manager = state.manager or await state.build()
        inc = await manager.handle(_event(event_id, company))
        assert inc is not None
        if wait:
            await manager.wait(inc.context_id)
        return inc.context_id


def _ids(company: str | None) -> set[str]:
    with company_scope.using(company):
        return set(state.manager.incidents) if state.manager else set()


@pytest.mark.asyncio
async def test_each_company_has_its_own_manager_and_incident_list() -> None:
    a = await _open("nubitour", "evt_a1")
    b = await _open("studyon", "evt_b1")
    c = await _open(None, "evt_c1")  # 요청 밖 = 캣북스
    assert _ids("nubitour") == {a} and _ids("studyon") == {b}
    assert _ids(None) == {c} == _ids("catbooks") == _ids("shop-demo")
    assert len({id(m) for m in state.managers.values()}) == 3


@pytest.mark.asyncio
async def test_saved_records_stay_in_each_companys_rows_after_a_restart() -> None:
    a = await _open("nubitour", "evt_a1")
    c = await _open(None, "evt_c1")
    # 다시 켠 것처럼 메모리 상태를 비우고, 저장 파일에서 회사마다 매니저를 미리 만든다
    for store in state.stores.values():
        store.close()
    state.managers.clear()
    state.stores.clear()
    await state.build()  # 기본 회사
    assert set(await state.prepare_companies()) == {"catbooks", "nubitour"}
    assert _ids("nubitour") == {a} and _ids(None) == {c}


@pytest.mark.asyncio
async def test_event_goes_to_the_company_in_its_envelope_over_http() -> None:
    client = TestClient(app)
    res = client.post(
        "/api/events", headers=AUTH, json=_event("evt_h1", "nubitour").model_dump(mode="json")
    )
    assert res.status_code == 200 and res.json()["data"]["context_id"]
    assert len(_ids("nubitour")) == 1
    assert _ids(None) == set() and _ids("studyon") == set()


def _a2a(client: TestClient, tenant: str | None) -> AgentResult:
    meta = RequestMeta(
        context_id="ctx_req_iso",
        token="mock:support",
        path=["support"],
        deadline=datetime.now(UTC) + timedelta(seconds=30),
        **({"tenant_id": tenant} if tenant else {}),
    )
    body = AgentRequest(meta=meta, skill="find_active_incident", input={"component": "payment"})
    return AgentResult.model_validate(client.post("/a2a", json=body.model_dump(mode="json")).json())


@pytest.mark.asyncio
async def test_a2a_skill_sees_only_the_request_companys_incidents() -> None:
    mine = await _open("nubitour", "evt_a1", wait=False)  # 열려 있는 동안만 찾힌다
    client = TestClient(app)
    assert _a2a(client, "nubitour").output["context_id"] == mine
    other = _a2a(client, "studyon")  # 처음 온 회사: 매니저를 만들고 비어 있다
    assert other.output["found"] is False
    assert _a2a(client, None).output["found"] is False  # 기본 회사 요청도 못 본다
    assert "studyon" in state.managers


def _session(company: str | None) -> dict[str, str]:
    token, _ = human_auth.issue_session(
        account_id="acc_1", name="tester", role="admin", company_id=company, gen=0
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_screen_reads_follow_the_login_session_company() -> None:
    mine = await _open("nubitour", "evt_a1")
    cat = await _open(None, "evt_c1")
    client = TestClient(app)

    def listed(headers: dict[str, str]) -> set[str]:
        return {
            row["context_id"]
            for row in client.get("/api/incidents", headers=headers).json()["data"]
        }

    assert listed(_session("nubitour")) == {mine}
    assert listed(_session("catbooks")) == {cat}
    assert listed({}) == {cat}  # 토큰 없는 읽기는 기본 회사(지금까지와 같다)
    assert client.get(f"/api/incidents/{mine}", headers=_session("catbooks")).status_code == 404
    assert client.get(f"/api/incidents/{mine}", headers=_session("nubitour")).status_code == 200


@pytest.mark.asyncio
async def test_shared_token_with_session_header_reads_that_company() -> None:
    """같은 출처 nginx는 읽기의 Authorization을 공용 토큰으로 바꿔 붙인다(11/25 실서버).
    화면이 같이 싣는 X-AX-Session으로 회사를 고른다. 위조된 머리는 무시(기본 회사)."""
    mine = await _open("nubitour", "evt_h1")
    cat = await _open(None, "evt_h2")
    client = TestClient(app)
    nubi = _session("nubitour")["Authorization"].removeprefix("Bearer ")

    def listed(headers: dict[str, str]) -> set[str]:
        return {
            row["context_id"]
            for row in client.get("/api/incidents", headers=headers).json()["data"]
        }

    shared = {"Authorization": "Bearer shared-human-token"}
    assert listed({**shared, "X-AX-Session": nubi}) == {mine}
    prefix, body, sig = nubi.split(".")
    forged = f"{prefix}.{body}.{'A' * len(sig)}"
    assert listed({**shared, "X-AX-Session": forged}) == {cat}
    assert listed(shared) == {cat}  # 머리 없음: 예전 그대로


@pytest.mark.asyncio
async def test_chat_threads_are_kept_per_company() -> None:
    with company_scope.using("nubitour"):
        threads.get_store().create("누비 대화", "u1")
    with company_scope.using("studyon"):
        assert threads.get_store().list_all() == []
    assert threads.get_store().list_all() == []  # 요청 밖(캣북스)
    with company_scope.using("nubitour"):
        assert [t["title"] for t in threads.get_store().list_all()] == ["누비 대화"]
        assert threads.get_store() is threads.get_store()  # 회사마다 한 번만 만든다
