"""승인된 배포를 infra `deploy_version`으로 한 번만 보낸다 (deploy_store.dispatch·refresh, REST).

infra는 가짜 호출 함수(`FakeInfra`)로 바꿔 끼운다. 실제 네트워크·배포 도구는 부르지 않는다.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, InputReason, TaskStatus
from dev_agent import hold_resume
from dev_agent.deploy import NO_TOOL_NOTE, UNKNOWN_NOTE
from dev_agent.deploy_store import INTERRUPTED_NOTE, DeployStore, set_deploy_store
from dev_agent.main import app

pytestmark = pytest.mark.role("dev")

BASE = "/api/planning"
RUN_URL = "https://github.com/acme/shop/actions/runs/901"


def _ok(req: AgentRequest) -> AgentResult:
    inp = req.input
    return AgentResult(
        meta=req.meta,
        status=TaskStatus.COMPLETED,
        output={
            "service": inp["service"],
            "version": inp["version"],
            "request_id": inp["request_id"],
            "tool": "github_actions",
            "run_id": "901",
            "url": RUN_URL,
            "status": "dispatched",
            "execution_id": "exe_d1",
            "grade": "A",
        },
    )


def _no_tool(req: AgentRequest) -> AgentResult:
    return AgentResult(
        meta=req.meta,
        status=TaskStatus.FAILED,
        outcome="no_deploy_tool",
        output={"message": "shop-api: 배포 도구가 연결되지 않았어요"},
    )


def _held(req: AgentRequest) -> AgentResult:
    return AgentResult(
        meta=req.meta,
        status=TaskStatus.INPUT_REQUIRED,
        reason_code=InputReason.HUMAN_INPUT,
        outcome="held",
        output={"review_id": "rev_d1", "execution_id": "exe_d2", "hold_reason": "over_threshold"},
    )


def _tool_error(req: AgentRequest) -> AgentResult:
    return AgentResult(
        meta=req.meta,
        status=TaskStatus.FAILED,
        outcome="deploy_failed",
        output={"tool": "jenkins", "code": "forbidden", "message": "배포 도구 호출 실패: → 403"},
    )


def _timeout(req: AgentRequest) -> AgentResult:
    return AgentResult(meta=req.meta, status=TaskStatus.CANCELED, outcome="TIMEOUT")


def _status(state: str):
    def answer(req: AgentRequest) -> AgentResult:
        return AgentResult(
            meta=req.meta,
            status=TaskStatus.COMPLETED,
            output={
                "service": req.input["service"],
                "run_id": req.input["run_id"],
                "tool": "github_actions",
                "status": state,
                "url": RUN_URL,
                "checked_at": "2026-10-21T05:30:00Z",
            },
        )

    return answer


class FakeInfra:
    """infra 에이전트 대역. skill마다 정해 둔 답을 차례로 준다 (마지막 답은 되풀이)."""

    def __init__(self, *deploy: Any, status: Any = None, tools: list[str] | None = None) -> None:
        self.deploy = list(deploy) or [_ok]
        self.status = status
        self.tools = tools if tools is not None else ["shop-api"]
        self.calls: list[AgentRequest] = []

    async def __call__(self, target: str, req: AgentRequest) -> AgentResult:
        assert target == "infra"
        self.calls.append(req)
        if req.skill == "deploy_tool_services":
            services = [{"service": t, "tool": "github_actions"} for t in self.tools]
            return AgentResult(
                meta=req.meta, status=TaskStatus.COMPLETED, output={"services": services}
            )
        if req.skill == "deploy_run_status":
            assert self.status is not None
            return self.status(req)
        answer = self.deploy.pop(0) if len(self.deploy) > 1 else self.deploy[0]
        return answer(req)

    def sent(self, skill: str = "deploy_version") -> list[AgentRequest]:
        return [c for c in self.calls if c.skill == skill]


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _install(*deploy: Any, status: Any = None, tools: list[str] | None = None) -> FakeInfra:
    fake = FakeInfra(*deploy, status=status, tools=tools)
    set_deploy_store(DeployStore(infra=fake))
    return fake


def _prod(client: TestClient) -> str:
    dep = client.post(f"{BASE}/deployments", json={"pr_numbers": [28], "env": "prod"}).json()
    assert dep["data"]["status"] == "requested"
    return dep["data"]["id"]


def test_approve_dispatches_once(client) -> None:
    infra = _install(_ok)
    dep_id = _prod(client)
    assert infra.sent() == []  # 승인 전에는 보내지 않는다
    res = client.post(f"{BASE}/deployments/{dep_id}/approve", json={"approved_by": "예진"})
    assert res.status_code == 200
    dep = res.json()["data"]
    assert (dep["status"], dep["executed"]) == ("running", True)
    run = dep["run"]
    assert (run["state"], run["tool"], run["run_id"], run["url"]) == (
        "dispatched",
        "github_actions",
        "901",
        RUN_URL,
    )
    assert (run["service"], run["version"]) == ("shop-api", "v2.4.1")
    [sent] = infra.sent()
    assert sent.input["request_id"] == f"{dep_id}.{run['request_id'].split('.')[-1]}"
    assert sent.input["request_id"] == run["request_id"]
    assert len(run["request_id"].split(".")[-1]) == 8  # 고유 꼬리
    assert sent.input["approved_by"] == "예진"
    assert sent.input["requester"] == "dev"
    assert sent.input["env"] == "prod"
    # 두 번 승인해도 다시 보내지 않는다
    again = client.post(f"{BASE}/deployments/{dep_id}/approve", json={"approved_by": "예진"})
    assert again.status_code == 409
    assert len(infra.sent()) == 1


def test_no_reason_deploy_waits_for_a_person(client) -> None:
    """승인 이유가 없어도(dev·보호 경로 없음) 운영 배포 도구라 저절로 보내지 않는다."""
    infra = _install(_ok)
    dep = client.post(f"{BASE}/deployments", json={"pr_numbers": [28]}).json()["data"]
    assert (dep["status"], dep["run"], dep["note"]) == (
        "ready",
        None,
        "배포 실행은 사람이 눌러야 해요",
    )
    assert infra.sent() == []
    # 사람이 '배포 실행'을 누르면 그 사람 승인으로 한 번 보낸다
    res = client.post(f"{BASE}/deployments/{dep['id']}/dispatch", json={"approved_by": "예진"})
    assert res.status_code == 200
    dep = res.json()["data"]
    assert (dep["status"], dep["approved_by"]) == ("running", "예진")
    assert [r.input["approved_by"] for r in infra.sent()] == ["예진"]
    again = client.post(f"{BASE}/deployments/{dep['id']}/dispatch", json={"approved_by": "예진"})
    assert (again.status_code, again.json()["error"]["code"]) == (409, "ALREADY_SENT")
    assert len(infra.sent()) == 1


def test_dispatch_needs_a_person(client, service) -> None:
    infra = _install(_ok)
    dep = client.post(f"{BASE}/deployments", json={"pr_numbers": [28]}).json()["data"]
    res = client.post(f"{BASE}/deployments/{dep['id']}/dispatch", json={})
    assert (res.status_code, res.json()["error"]["code"]) == (400, "APPROVER_REQUIRED")
    agent = {"Authorization": "Bearer mock:planning"}
    res = client.post(
        f"{BASE}/deployments/{dep['id']}/dispatch", json={"approved_by": "x"}, headers=agent
    )
    assert res.status_code == 403  # 에이전트 토큰으로는 누를 수 없다
    # 저장소 안에서도 사람 승인이 없으면 보내지 않는다
    store = set_deploy_store(DeployStore(infra=infra))
    ready = asyncio.run(store.request(service, [28], "dev"))
    approved = ready.model_copy(update={"status": "approved"})
    store._put(approved)
    asyncio.run(store.dispatch(service, ready.id))
    assert infra.sent() == []


def test_handed_to_human_is_recorded(client) -> None:
    def handed(req: AgentRequest) -> AgentResult:
        return AgentResult(
            meta=req.meta,
            status=TaskStatus.INPUT_REQUIRED,
            reason_code=InputReason.HUMAN_INPUT,
            outcome="handed_to_human",
            output={},
        )

    _install(handed)
    dep_id = _prod(client)
    dep = client.post(f"{BASE}/deployments/{dep_id}/approve", json={"approved_by": "예진"})
    run = dep.json()["data"]["run"]
    assert (run["state"], run["reason"]) == ("handed", "사람 할 일함으로 넘어감 — 사람이 실행")


def test_no_tool_is_recorded_not_faked(client) -> None:
    _install(_no_tool)
    dep_id = _prod(client)
    dep = client.post(f"{BASE}/deployments/{dep_id}/approve", json={"approved_by": "예진"}).json()
    dep = dep["data"]
    assert (dep["status"], dep["executed"]) == ("approved", False)
    assert dep["run"]["state"] == "no_tool"
    assert dep["note"] == NO_TOOL_NOTE
    res = client.post(f"{BASE}/deployments/{dep_id}/check")
    assert res.status_code == 409  # 배포되지 않은 배포는 배포 후 확인을 하지 않는다


@pytest.mark.parametrize(
    ("answer", "state", "words"),
    [
        (_tool_error, "failed", "403"),
        (_timeout, "unknown", "먼저 확인"),
        (
            lambda req: AgentResult(meta=req.meta, status=TaskStatus.FAILED, outcome="http_502"),
            "unknown",
            "시작됐을 수",
        ),
        (
            lambda req: AgentResult(meta=req.meta, status=TaskStatus.FAILED, outcome="unreachable"),
            "unknown",
            "시작됐을 수",
        ),
        (
            lambda req: AgentResult(
                meta=req.meta, status=TaskStatus.FAILED, outcome="INVALID_OUTPUT"
            ),
            "unknown",
            "시작됐을 수",
        ),
        (
            lambda req: AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output={"x": 1}),
            "unknown",
            "시작됐을 수",
        ),
        (
            lambda req: AgentResult(
                meta=req.meta, status=TaskStatus.REJECTED, outcome="FORBIDDEN_SKILL"
            ),
            "failed",
            "FORBIDDEN_SKILL",
        ),
    ],
)
def test_failure_is_recorded_with_reason(client, answer, state: str, words: str) -> None:
    infra = _install(answer)
    dep_id = _prod(client)
    dep = client.post(f"{BASE}/deployments/{dep_id}/approve", json={"approved_by": "예진"}).json()
    run = dep["data"]["run"]
    assert run["state"] == state
    assert words in run["reason"]
    assert dep["data"]["executed"] is False
    assert len(infra.sent()) == 1


def test_unreachable_and_exception_are_not_retried(service) -> None:
    calls: list[str] = []

    async def boom(target: str, req: AgentRequest) -> AgentResult:
        calls.append(req.skill)
        raise RuntimeError("down")

    store = set_deploy_store(DeployStore(infra=boom))
    dep = asyncio.run(store.request(service, [28], "prod"))
    dep = asyncio.run(store.approve(service, dep.id, "예진"))
    assert dep.run is not None and dep.run.state == "unknown"
    assert dep.run.reason == UNKNOWN_NOTE
    asyncio.run(store.dispatch(service, dep.id))
    assert calls == ["deploy_version"]


def test_concurrent_dispatch_sends_once(service) -> None:
    gate = asyncio.Event()
    calls: list[str] = []

    async def slow(target: str, req: AgentRequest) -> AgentResult:
        calls.append(req.input["request_id"])
        await gate.wait()
        return _ok(req)

    store = set_deploy_store(DeployStore(infra=slow))

    async def both() -> None:
        dep = await store.request(service, [28], "prod")
        first = asyncio.create_task(store.approve(service, dep.id, "예진"))
        while not calls:  # 첫 요청이 infra에서 기다리는 중
            await asyncio.sleep(0)
        # 보내는 중에 또 보내기·또 승인 → 보내지 않는다
        second = await store.dispatch(service, dep.id)
        assert second.run is not None and second.run.state == "sending"
        gate.set()
        await first

    asyncio.run(both())
    assert len(calls) == 1


def test_restart_does_not_redispatch(tmp_path, monkeypatch: pytest.MonkeyPatch, service) -> None:
    monkeypatch.setenv("PLANNING_STATE_DIR", str(tmp_path))
    infra = FakeInfra(_ok)
    first = set_deploy_store(DeployStore(infra=infra))
    dep = asyncio.run(first.request(service, [28], "prod"))
    asyncio.run(first.approve(service, dep.id, "예진"))
    assert len(infra.sent()) == 1
    again = DeployStore(infra=infra)
    got = again.get(dep.id)
    assert got is not None and got.run is not None and got.run.state == "dispatched"
    asyncio.run(again.dispatch(service, dep.id))
    assert len(infra.sent()) == 1  # 다시 켜도 다시 보내지 않는다


def test_restart_while_sending_becomes_unknown(
    tmp_path, monkeypatch: pytest.MonkeyPatch, service
) -> None:
    """보내던 중 꺼진 배포는 결과를 모르니 다시 보내지 않고 사람이 도구에서 확인한다."""
    monkeypatch.setenv("PLANNING_STATE_DIR", str(tmp_path))
    seen: list[str] = []

    async def crash(target: str, req: AgentRequest) -> AgentResult:
        seen.append(req.skill)
        raise asyncio.CancelledError  # 보내던 중 서버가 꺼졌다

    first = set_deploy_store(DeployStore(infra=crash))
    dep = asyncio.run(first.request(service, [28], "prod"))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(first.approve(service, dep.id, "예진"))
    infra = FakeInfra(_ok)
    again = DeployStore(infra=infra)
    got = again.get(dep.id)
    assert got is not None and got.run is not None
    assert (got.run.state, got.run.reason) == ("unknown", INTERRUPTED_NOTE)
    asyncio.run(again.dispatch(service, dep.id))
    assert infra.sent() == []
    assert seen == ["deploy_version"]


def test_held_then_safety_approval_resumes_once(service) -> None:
    infra = FakeInfra(_held, _ok)
    store = set_deploy_store(DeployStore(infra=infra))
    dep = asyncio.run(store.request(service, [28], "prod"))
    dep = asyncio.run(store.approve(service, dep.id, "예진"))
    assert dep.run is not None and dep.run.state == "held"
    assert dep.run.review_id == "rev_d1"
    assert "안전 확인" in (dep.note or "")
    row = service.holds.get("rev_d1")
    assert row is not None and (row.kind, row.args) == ("deploy_dispatch", {"dep_id": dep.id})
    # 사람이 안전 확인에서 승인 → 같은 요청 id로 한 번 다시 보낸다
    done = asyncio.run(hold_resume.decide(service, "rev_d1", "approved", "예진"))
    assert done is not None and done.status == "done", done
    got = store.get(dep.id)
    assert got is not None and got.status == "running"
    assert got.run is not None and got.run.state == "dispatched"
    sent = infra.sent()
    assert dep.run.request_id is not None and dep.run.request_id.startswith(f"{dep.id}.")
    assert [r.input["request_id"] for r in sent] == [dep.run.request_id] * 2
    # 같은 승인이 또 와도 다시 보내지 않는다
    asyncio.run(hold_resume.decide(service, "rev_d1", "approved", "예진"))
    assert len(infra.sent()) == 2


def test_held_then_rejected_does_not_send(service) -> None:
    infra = FakeInfra(_held)
    store = set_deploy_store(DeployStore(infra=infra))
    dep = asyncio.run(store.request(service, [28], "prod"))
    asyncio.run(store.approve(service, dep.id, "예진"))
    asyncio.run(hold_resume.decide(service, "rev_d1", "rejected", "예진"))
    assert len(infra.sent()) == 1
    got = store.get(dep.id)
    assert got is not None and got.status == "approved" and got.executed is False


@pytest.mark.parametrize(("state", "status"), [("succeeded", "succeeded"), ("failed", "failed")])
def test_refresh_reads_tool_status(client, state: str, status: str) -> None:
    infra = _install(_ok, status=_status(state))
    dep_id = _prod(client)
    client.post(f"{BASE}/deployments/{dep_id}/approve", json={"approved_by": "예진"})
    res = client.post(f"{BASE}/deployments/{dep_id}/refresh")
    assert res.status_code == 200
    dep = res.json()["data"]
    assert dep["status"] == status
    assert dep["run"]["run_status"] == state
    assert dep["run"]["checked_at"] is not None
    [asked] = infra.sent("deploy_run_status")
    assert asked.input == {"service": "shop-api", "run_id": "901"}
    if status == "succeeded":  # 끝난 배포는 배포 후 확인을 할 수 있다
        assert client.post(f"{BASE}/deployments/{dep_id}/check").status_code == 200


def test_refresh_running_and_unknown_keep_status(client) -> None:
    _install(_ok, status=_status("running"))
    dep_id = _prod(client)
    client.post(f"{BASE}/deployments/{dep_id}/approve", json={"approved_by": "예진"})
    dep = client.post(f"{BASE}/deployments/{dep_id}/refresh").json()["data"]
    assert (dep["status"], dep["run"]["run_status"]) == ("running", "running")


def test_refresh_without_dispatch_is_refused(client) -> None:
    _install(_no_tool)
    dep_id = _prod(client)
    client.post(f"{BASE}/deployments/{dep_id}/approve", json={"approved_by": "예진"})
    res = client.post(f"{BASE}/deployments/{dep_id}/refresh")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "NOT_DISPATCHED"


HUMAN = {"Authorization": "Bearer human:demo"}


def test_missing_version_is_not_sent(client, service) -> None:
    infra = _install(_ok)
    pr = next(p for p in service.state.prs if p.number == 28)
    service.state.prs = [
        p.model_copy(update={"deployed_version": None}) if p is pr else p for p in service.state.prs
    ]
    dep = client.post(f"{BASE}/deployments", json={"pr_numbers": [28]}).json()["data"]
    dep = client.post(f"{BASE}/deployments/{dep['id']}/dispatch", json={"approved_by": "예진"})
    run = dep.json()["data"]["run"]
    assert run["state"] == "failed" and run["code"] == "no_version"
    assert infra.sent() == []


def test_version_override_is_human_only_and_needs_a_tool(client) -> None:
    infra = _install(_ok, tools=["shop-api"])
    body = {"pr_numbers": [28], "version": "shop-api v2.5.0"}
    agent = client.post(
        f"{BASE}/deployments", json=body, headers={"Authorization": "Bearer mock:planning"}
    )
    assert agent.status_code == 403  # AI가 버전을 직접 정하지 않는다
    res = client.post(f"{BASE}/deployments", json=body, headers=HUMAN)
    assert res.status_code == 200, res.json()
    dep = res.json()["data"]
    assert (dep["status"], dep["version"]) == ("ready", "shop-api v2.5.0")
    assert infra.sent() == []  # 버전을 정해도 저절로 보내지 않는다
    client.post(
        f"{BASE}/deployments/{dep['id']}/dispatch", json={"approved_by": "예진"}, headers=HUMAN
    )
    assert infra.sent()[0].input["version"] == "v2.5.0"
    # 배포 도구가 없는 서비스는 받지 않는다
    other = client.post(
        f"{BASE}/deployments", json={"pr_numbers": [28], "version": "other-api v1"}, headers=HUMAN
    )
    assert (other.status_code, other.json()["error"]["code"]) == (409, "NO_DEPLOY_TOOL")
    for bad in ("v2", "shop-api " + "v" * 121, 3):
        res = client.post(
            f"{BASE}/deployments", json={"pr_numbers": [28], "version": bad}, headers=HUMAN
        )
        assert res.status_code == 400, bad


def test_approval_text_names_service_and_version(client, service) -> None:
    _install(_ok)
    seen: list[tuple[str, dict, str]] = []
    real = service._gate

    async def spy(action, params, meta, evidence):
        seen.append((action, params, evidence.data_summary))
        return await real(action, params, meta, evidence)

    service._gate = spy
    dep_id = _prod(client)
    client.post(f"{BASE}/deployments/{dep_id}/approve", json={"approved_by": "예진"})
    [(_, params, summary)] = [x for x in seen if x[0] == "approve_deploy"]
    assert "shop-api v2.4.1" in summary and "운영 배포 도구로 실행" in summary
    assert params["version"] == "shop-api v2.4.1"


def test_mock_mode_without_infra_does_not_call(client) -> None:
    """목 모드(테스트 전용)에서 부를 곳을 끼우지 않으면 infra를 부르지 않는다 (실행 안 함)."""
    set_deploy_store(DeployStore())
    dep = client.post(f"{BASE}/deployments", json={"pr_numbers": [28]}).json()["data"]
    dep = client.post(f"{BASE}/deployments/{dep['id']}/dispatch", json={"approved_by": "예진"})
    dep = dep.json()["data"]
    assert (dep["status"], dep["executed"]) == ("approved", False)
    assert dep["run"]["code"] == "mock_mode"


def test_approved_but_not_sent_is_marked_on_load_and_sent_by_hand(
    tmp_path, monkeypatch: pytest.MonkeyPatch, service, client
) -> None:
    monkeypatch.setenv("PLANNING_STATE_DIR", str(tmp_path))
    infra = FakeInfra(_ok)
    first = set_deploy_store(DeployStore(infra=infra))
    dep = asyncio.run(first.request(service, [28], "prod"))
    # 승인만 저장되고 보내기 전에 꺼졌다 (예전 기록·중간에 멈춤)
    first._put(dep.model_copy(update={"status": "approved", "approved_by": "예진"}))
    first._save()
    again = set_deploy_store(DeployStore(infra=infra))
    got = again.get(dep.id)
    assert got is not None and got.run is not None and got.run.state == "not_sent"
    assert infra.sent() == []  # 다시 켜도 저절로 보내지 않는다
    res = client.post(f"{BASE}/deployments/{dep.id}/dispatch", json={"approved_by": "병환"})
    assert res.json()["data"]["run"]["state"] == "dispatched"
    assert [r.input["approved_by"] for r in infra.sent()] == ["예진"]
    res = client.post(f"{BASE}/deployments/{dep.id}/dispatch", json={"approved_by": "병환"})
    assert res.status_code == 409
    assert len(infra.sent()) == 1


def test_refresh_takes_jenkins_build_number(client) -> None:
    def queued(req: AgentRequest) -> AgentResult:
        res = _ok(req)
        res.output.update(
            {"tool": "jenkins", "run_id": "queue-12", "url": "https://ci.example/queue/item/12/"}
        )
        return res

    answers = iter([("queue-12", "unknown", True), ("57", "running", False)])

    def status(req: AgentRequest) -> AgentResult:
        run_id, state, resolving = next(answers)
        out = _status(state)(req)
        out.output.update({"run_id": run_id, "tool": "jenkins", "resolving": resolving})
        return out

    _install(queued, status=status)
    dep_id = _prod(client)
    client.post(f"{BASE}/deployments/{dep_id}/approve", json={"approved_by": "예진"})
    run = client.post(f"{BASE}/deployments/{dep_id}/refresh").json()["data"]["run"]
    assert (run["run_id"], run["resolving"]) == ("queue-12", True)
    run = client.post(f"{BASE}/deployments/{dep_id}/refresh").json()["data"]["run"]
    assert (run["run_id"], run["resolving"], run["run_status"]) == ("57", False, "running")
