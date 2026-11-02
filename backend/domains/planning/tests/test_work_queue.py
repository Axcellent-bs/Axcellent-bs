"""구독형 일감 대기열: 개발자 본인 Claude가 MCP로 작업을 가져가 진행·PR을 알린다.

실제 GitHub·LLM은 부르지 않는다. 형상관리는 목 부품이고, 개발자 PR은 그 부품에 직접 올린다.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template.llm import LLMSettings
from planning_agent import coder, repo_routing, vcs, work_queue
from planning_agent.dev_service import DevService
from planning_agent.domain.clock import kst
from planning_agent.domain.service import ActionError, PlanningService
from planning_agent.main import app
from planning_agent.runtime import set_service
from planning_agent.vcs import MockVcsAdapter

pytestmark = pytest.mark.role("dev")  # 분리 3단계: 개발 서비스 역할로 돈다

BANNER = "shop-web/src/components/Banner.tsx"


def run(coro):
    return asyncio.run(coro)


class Clock:
    """손으로 움직이는 시계 (임대 만료 확인용)."""

    def __init__(self) -> None:
        self.now = kst("10-22 10:42")

    def __call__(self):
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def svc(clock: Clock) -> PlanningService:
    return set_service(DevService(clock=clock))


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch):
    for name in ("PLANNING_VCS_KIND", "PLANNING_VCS_REPO", "PLANNING_VCS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("PLANNING_LEASE_MINUTES", raising=False)
    vcs._screen = None
    a = MockVcsAdapter()
    a.seed_files({BANNER: "export function Banner() {\n  return null\n}\n"})
    vcs.set_vcs(a)
    repo_routing.set_llm(None)
    coder.set_llm(None)  # 구독형은 서버 LLM 없이도 만들어져야 한다
    yield a
    vcs._screen = None
    vcs.set_vcs(None)
    coder.reset_llm()
    repo_routing.reset_llm()


def _new(svc: PlanningService) -> str:
    out = run(
        svc.create_dev_task(
            title="신간 배너 문구 바꾸기",
            plan="메인 배너 문구를 신간 안내로 바꾼다.",
            done="배너에 신간 문구가 보인다\n모바일에서 잘리지 않는다",
            paths=["shop-web/src/components"],
            mode="subscription",
        )
    )
    return out.output["task_id"]


def _coding(svc: PlanningService, task_id: str):
    task = next(t for t in svc.state.tasks if t.id == task_id)
    assert task.coding is not None
    return task, task.coding


def _open_pr(adapter: MockVcsAdapter, title: str = "T-D1: 신간 배너 문구") -> int:
    files = {BANNER: "export function Banner() {\n  return <div>신간</div>\n}\n"}
    return run(adapter.open_change(branch="gen/t-d1", title=title, body="", files=files)).number


def test_subscription_task_does_not_spawn_coder(svc: PlanningService, adapter) -> None:
    task_id = _new(svc)
    task, cr = _coding(svc, task_id)
    assert (cr.mode, cr.status, cr.model) == ("subscription", "queued", None)
    assert task.owner == "개발자 Claude"
    assert task_id not in svc._coding  # 서버 에이전트를 띄우지 않았다
    assert adapter.writes == []
    rows = work_queue.available(svc, "jiwon")
    assert [r["task_id"] for r in rows] == [task_id]
    assert rows[0]["done"] == ["배너에 신간 문구가 보인다", "모바일에서 잘리지 않는다"]
    assert rows[0]["paths"] == ["shop-web/src/components"]


def test_claim_progress_pr_then_merge_sync(svc: PlanningService, adapter, clock: Clock) -> None:
    task_id = _new(svc)
    spec = run(work_queue.claim(svc, task_id, "jiwon"))
    assert spec["branch"] == "gen/t-d1" and spec["pr_title"].startswith("T-D1: ")
    assert "T-D1" in spec["commit_rule"] and spec["plan"].startswith("메인 배너")
    task, cr = _coding(svc, task_id)
    assert (cr.status, cr.claimed_by, task.owner) == ("claimed", "jiwon", "jiwon")
    assert cr.lease_until == clock.now + timedelta(minutes=30)

    clock.now += timedelta(minutes=20)
    run(work_queue.progress(svc, task_id, "jiwon", "배너 컴포넌트 고치는 중"))
    _, cr = _coding(svc, task_id)
    assert (cr.status, cr.progress) == ("writing", "배너 컴포넌트 고치는 중")
    assert cr.lease_until == clock.now + timedelta(minutes=30)  # 임대가 늘었다

    number = _open_pr(adapter)
    out = run(work_queue.report_pr(svc, task_id, "jiwon", number))
    task, cr = _coding(svc, task_id)
    pr = next(p for p in svc.state.prs if p.id == out["pr_id"])
    assert (cr.status, cr.pr, cr.lease_until, task.state) == ("opened", pr.id, None, "PR 검수")
    assert (pr.vcs_number, pr.branch, pr.task) == (number, "gen/t-d1", task_id)
    assert pr.gate.result == "통과"
    assert pr.changed_paths == [BANNER] and cr.files[0].path == BANNER
    assert any(g.action == "generate_pr" for g in svc.gate_calls)

    # 임대 시간이 지나도 PR이 열린 작업은 되돌리지 않는다
    clock.now += timedelta(hours=2)
    assert run(work_queue.expire_leases(svc)) == 0

    # 형상관리에서 직접 머지 → 기존 PR 맞추기가 완료로 맞춘다
    run(adapter.merge_change(number))
    assert run(svc.sync_prs()) == 1
    task, cr = _coding(svc, task_id)
    assert (task.state, cr.status) == ("완료", "merged")


def test_lease_expiry_requeues(svc: PlanningService, adapter, clock: Clock) -> None:
    task_id = _new(svc)
    run(work_queue.claim(svc, task_id, "jiwon"))
    clock.now += timedelta(minutes=29)
    assert run(work_queue.expire_leases(svc)) == 0
    clock.now += timedelta(minutes=2)
    assert run(work_queue.expire_leases(svc)) == 1
    task, cr = _coding(svc, task_id)
    assert (cr.status, cr.claimed_by, cr.lease_until) == ("queued", None, None)
    assert "진행 보고 없음" in (cr.error or "") and task.owner == "개발자 Claude"
    with pytest.raises(ActionError) as err:  # 만료된 사람은 보고할 수 없다 → 다시 claim
        run(work_queue.progress(svc, task_id, "jiwon", "계속"))
    assert err.value.code == "NOT_CLAIMED"
    run(work_queue.claim(svc, task_id, "minsu"))  # 다른 사람이 가져갈 수 있다
    assert _coding(svc, task_id)[1].claimed_by == "minsu"


def test_lease_minutes_from_env(svc, adapter, clock: Clock, monkeypatch) -> None:
    monkeypatch.setenv("PLANNING_LEASE_MINUTES", "5")
    task_id = _new(svc)
    run(work_queue.claim(svc, task_id, "jiwon"))
    assert _coding(svc, task_id)[1].lease_until == clock.now + timedelta(minutes=5)


def test_release_back_to_queue(svc: PlanningService, adapter) -> None:
    task_id = _new(svc)
    run(work_queue.claim(svc, task_id, "jiwon"))
    with pytest.raises(ActionError) as err:
        run(work_queue.release(svc, task_id, "minsu"))
    assert err.value.code == "NOT_CLAIMANT"
    run(work_queue.release(svc, task_id, "jiwon", "다른 급한 일"))
    _, cr = _coding(svc, task_id)
    assert (cr.status, cr.claimed_by) == ("queued", None)
    lines = [a.text for a in svc.state.activity if a.point == "dev_task_release"]
    assert any("다른 급한 일" in x for x in lines)


def test_cancel_then_retry_stays_in_queue(svc: PlanningService, adapter) -> None:
    """구독형 작업은 중단 뒤 재개해도 서버 에이전트를 띄우지 않고 대기열로만 돌아간다."""
    task_id = _new(svc)
    run(work_queue.claim(svc, task_id, "jiwon"))
    run(svc.cancel_dev_task(task_id))
    run(svc.retry_dev_task(task_id))
    _, cr = _coding(svc, task_id)
    assert (cr.mode, cr.status, cr.claimed_by) == ("subscription", "queued", None)
    assert task_id not in svc._coding


def test_claim_already_claimed_fails(svc: PlanningService, adapter, clock: Clock) -> None:
    task_id = _new(svc)
    run(work_queue.claim(svc, task_id, "jiwon"))
    with pytest.raises(ActionError) as err:
        run(work_queue.claim(svc, task_id, "minsu"))
    assert err.value.code == "ALREADY_CLAIMED"
    assert _coding(svc, task_id)[1].claimed_by == "jiwon"
    # 같은 사람이 다시 부르면 임대만 늘린다 (관문 claim은 한 번)
    clock.now += timedelta(minutes=10)
    run(work_queue.claim(svc, task_id, "jiwon"))
    assert sum(1 for g in svc.gate_calls if g.action == "dev_task_claim") == 1
    assert _coding(svc, task_id)[1].lease_until == clock.now + timedelta(minutes=30)


def test_report_pr_needs_task_id_in_title(svc: PlanningService, adapter) -> None:
    task_id = _new(svc)
    run(work_queue.claim(svc, task_id, "jiwon"))
    number = _open_pr(adapter, title="배너 문구 바꾸기")
    with pytest.raises(ActionError) as err:
        run(work_queue.report_pr(svc, task_id, "jiwon", number))
    assert err.value.code == "INVALID_INPUT"
    assert svc.state.prs == [] or all(p.task != task_id for p in svc.state.prs)


def test_agent_task_is_not_in_queue(svc: PlanningService, adapter) -> None:
    """서버 에이전트 작업(기본)은 대기열에 안 나오고 가져갈 수 없다."""

    class NoLLM:
        settings = LLMSettings(model="fake-coder", base_url="http://llm.invalid")

    coder.set_llm(NoLLM())
    svc._spawn_coder = lambda task_id: None  # type: ignore[method-assign]
    out = run(svc.create_dev_task(title="다른 작업", plan="서버 에이전트가 한다."))
    task_id = out.output["task_id"]
    assert _coding(svc, task_id)[1].mode == "agent"
    assert work_queue.available(svc, "jiwon") == []
    with pytest.raises(ActionError) as err:
        run(work_queue.claim(svc, task_id, "jiwon"))
    assert err.value.code == "NOT_SUBSCRIPTION"


# ---------------------------------------------------------------- REST·MCP


def test_rest_and_mcp_need_human_token(svc: PlanningService, adapter) -> None:
    _new(svc)
    client = TestClient(app)
    none = {"Authorization": ""}
    agent = {"Authorization": "Bearer mock:infra"}
    assert client.get("/api/planning/work/tasks", headers=none).status_code == 401
    assert client.get("/api/planning/work/tasks", headers=agent).status_code == 403
    assert client.post("/api/planning/work/tasks/T-D1/claim", headers=none).status_code == 401
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert client.post("/api/planning/mcp", json=body, headers=none).status_code == 401
    assert client.post("/api/planning/mcp", json=body, headers=agent).status_code == 403


def test_rest_flow(svc: PlanningService, adapter) -> None:
    task_id = _new(svc)
    client = TestClient(app)
    me = {"Authorization": "Bearer human:tester"}  # GET에는 목 토큰을 안 붙인다 (conftest)
    rows = client.get("/api/planning/work/tasks", headers=me).json()["data"]
    assert [r["task_id"] for r in rows] == [task_id]
    got = client.post(f"/api/planning/work/tasks/{task_id}/claim").json()["data"]
    assert got["claimed_by"] == "tester" and got["branch"] == "gen/t-d1"
    other = {"Authorization": "Bearer human:other"}
    res = client.post(f"/api/planning/work/tasks/{task_id}/claim", headers=other)
    assert (res.status_code, res.json()["error"]["code"]) == (409, "ALREADY_CLAIMED")
    res = client.post(f"/api/planning/work/tasks/{task_id}/progress", json={"text": "작업 중"})
    assert res.json()["data"]["status"] == "writing"
    res = client.post(f"/api/planning/work/tasks/{task_id}/pr", json={"number": "x"})
    assert res.status_code == 400
    number = _open_pr(adapter)
    res = client.post(f"/api/planning/work/tasks/{task_id}/pr", json={"number": number})
    assert res.status_code == 200 and res.json()["data"]["vcs_number"] == number
    assert _coding(svc, task_id)[1].status == "opened"


def test_mcp_tools(svc: PlanningService, adapter) -> None:
    task_id = _new(svc)
    client = TestClient(app)

    def rpc(method: str, params: dict | None = None, id_: int | None = 1):
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if id_ is not None:
            msg["id"] = id_
        return client.post("/api/planning/mcp", json=msg)

    init = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}}).json()
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert "tools" in init["result"]["capabilities"]
    assert rpc("notifications/initialized", id_=None).status_code == 202
    names = [t["name"] for t in rpc("tools/list").json()["result"]["tools"]]
    assert names == ["list_tasks", "claim_task", "report_progress", "report_pr", "release_task"]

    listed = rpc("tools/call", {"name": "list_tasks", "arguments": {}}).json()["result"]
    assert listed["isError"] is False and task_id in listed["content"][0]["text"]
    claimed = rpc("tools/call", {"name": "claim_task", "arguments": {"task_id": task_id}})
    result = claimed.json()["result"]
    assert result["isError"] is False and result["structuredContent"]["claimed_by"] == "tester"
    bad = rpc("tools/call", {"name": "report_pr", "arguments": {"task_id": task_id}}).json()
    assert bad["result"]["isError"] is True
    released = rpc("tools/call", {"name": "release_task", "arguments": {"task_id": task_id}})
    assert released.json()["result"]["structuredContent"]["status"] == "queued"
    assert rpc("nope").json()["error"]["code"] == -32601
    assert client.get("/api/planning/mcp").status_code == 405
