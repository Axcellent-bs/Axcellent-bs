"""infra `deploy_version`(B)의 dev 교차검증 `verify_deploy_request` (#1617 사후 리뷰).

배포 기록 DEP-n이 실제로 `approved`이고 승인자·서비스·버전이 요청과 같을 때만 통과한다.
safety는 실행 요청 그대로(`execution_id`·`action`·`params`) 부른다 (`ax_safety.gate._cross_verify`).
infra는 가짜 호출 함수로 바꿔 끼운다. 실제 네트워크·배포 도구는 부르지 않는다.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from dev_agent import handlers
from dev_agent.deploy import Deployment, request_id
from dev_agent.deploy_store import DeployStore, set_deploy_store
from dev_agent.main import app

pytestmark = pytest.mark.role("dev")


def _no_tool(req: AgentRequest) -> AgentResult:
    """배포 도구 미연결: 기록은 `approved`(실행 안 함)로 남는다."""
    return AgentResult(
        meta=req.meta,
        status=TaskStatus.FAILED,
        outcome="no_deploy_tool",
        output={"message": "shop-api: 배포 도구가 연결되지 않았어요"},
    )


class Infra:
    def __init__(self, answer: Any = _no_tool) -> None:
        self.answer = answer
        self.calls: list[AgentRequest] = []

    async def __call__(self, target: str, req: AgentRequest) -> AgentResult:
        assert target == "infra"
        self.calls.append(req)
        result = self.answer(req)
        return await result if asyncio.iscoroutine(result) else result


def _review(params: dict[str, Any], action: str = "deploy_version") -> dict[str, Any]:
    """safety 교차검증 입력 (gate `_cross_verify`와 같은 모양)."""
    return {
        "execution_id": "exe_dep1",
        "action": action,
        "requester": "incident",
        "params": params,
        "computed_amount": None,
        "threshold_version": "v1",
    }


def _params(dep: Deployment, **over: Any) -> dict[str, Any]:
    """infra가 execute(deploy_version)에 싣는 params (DeployVersionInput + tool)."""
    base = {
        "service": "shop-api",
        "version": "v2.4.1",
        "request_id": request_id(dep),
        "env": dep.env,
        "requester": "dev",
        "approved_by": "예진",
        "tool": "github_actions",
    }
    return {**base, **over}


def _verify(a2a_body, payload: dict[str, Any], token: str = "mock:safety") -> dict[str, Any]:
    body = a2a_body("verify_deploy_request", payload, token=token, context_id="ctx_dep_0001")
    return TestClient(app).post("/a2a", json=body).json()


def _approved(service) -> Deployment:
    """사람이 승인했지만 도구가 없어 실행되지 않은 배포 (상태 approved, 승인자 예진)."""
    store = set_deploy_store(DeployStore(infra=Infra()))
    dep = asyncio.run(store.request(service, [28], "prod"))
    dep = asyncio.run(store.approve(service, dep.id, "예진"))
    assert (dep.status, dep.approved_by, dep.version) == ("approved", "예진", "shop-api v2.4.1")
    return dep


def _rejected(out: dict[str, Any], reason: str) -> None:
    assert (out["status"], out["outcome"]) == ("completed", "rejected_by_review"), out
    assert out["output"]["approved"] is False
    assert out["output"]["reason_code"] == reason


def test_all_same_passes(a2a_body, service) -> None:
    dep = _approved(service)
    out = _verify(a2a_body, _review(_params(dep)))
    assert (out["status"], out["outcome"]) == ("completed", None), out
    assert out["output"] == {
        "approved": True,
        "reason_code": "deploy_approved",
        "deployment_id": dep.id,
    }
    assert f"deploy_request:{request_id(dep)}" in out["evidence"]["ref_ids"]
    assert "execution:exe_dep1" in out["evidence"]["ref_ids"]
    # 계약 형식으로 불러도 같은 판정
    contract = {k: _params(dep)[k] for k in ("request_id", "service", "version", "approved_by")}
    again = _verify(a2a_body, contract)
    assert again["output"]["approved"] is True


def test_not_approved_is_rejected(a2a_body, service) -> None:
    store = set_deploy_store(DeployStore(infra=Infra()))
    dep = asyncio.run(store.request(service, [28], "prod"))
    assert dep.status == "requested"  # 사람 승인 전
    out = _verify(a2a_body, _review(_params(dep)))
    _rejected(out, "deploy_not_approved")
    assert out["output"]["deployment_id"] == dep.id


def test_approver_mismatch_is_rejected(a2a_body, service) -> None:
    dep = _approved(service)
    for who in ("다른 사람", None, "  "):
        out = _verify(a2a_body, _review(_params(dep, approved_by=who)))
        _rejected(out, "approver_mismatch")


@pytest.mark.parametrize(
    "over",
    [{"service": "other-api"}, {"version": "v9.9.9"}, {"service": "other-api", "version": "v1"}],
)
def test_service_or_version_mismatch_is_rejected(a2a_body, service, over) -> None:
    dep = _approved(service)
    _rejected(_verify(a2a_body, _review(_params(dep, **over))), "target_mismatch")


@pytest.mark.parametrize("rid", ["DEP-999.1a2b3c4d", "DEP-1.deadbeef", "WRONG"])
def test_missing_deployment_is_rejected(a2a_body, service, rid) -> None:
    dep = _approved(service)
    if rid == "DEP-1.deadbeef":  # 같은 번호라도 고유 꼬리가 다르면 다른 기록
        rid = f"{dep.id}.deadbeef"
    out = _verify(a2a_body, _review(_params(dep, request_id=rid)))
    _rejected(out, "deploy_not_found")
    assert out["output"]["deployment_id"] is None


def test_unreadable_input_is_failed_not_approved(a2a_body, service) -> None:
    dep = _approved(service)
    wrong_action = _verify(a2a_body, _review(_params(dep), action="rollback_deployment"))
    assert (wrong_action["status"], wrong_action["outcome"]) == ("failed", "invalid_input")
    no_request_id = _params(dep)
    no_request_id.pop("request_id")
    missing = _verify(a2a_body, _review(no_request_id))
    assert missing["status"] == "failed"


def test_only_safety_can_call(a2a_body, service) -> None:
    dep = _approved(service)
    for token in ("mock:infra", "mock:dev", "mock:orchestrator"):
        out = _verify(a2a_body, _review(_params(dep)), token=token)
        assert (out["status"], out["outcome"]) == ("rejected", "FORBIDDEN_SKILL"), token


def test_verify_does_not_change_state(a2a_body, service) -> None:
    dep = _approved(service)
    store = set_deploy_store(DeployStore(infra=Infra()))
    store.deployments = [dep]
    before = [d.model_dump(mode="json") for d in store.deployments]
    _verify(a2a_body, _review(_params(dep)))
    _verify(a2a_body, _review(_params(dep, approved_by="다른 사람")))
    assert [d.model_dump(mode="json") for d in store.deployments] == before


def test_passes_while_dev_is_sending_the_approved_deploy(a2a_body, service) -> None:
    """실제 흐름: dev가 보내는 동안(approved + sending) infra 관문이 dev 검증을 부르면 통과한다."""
    seen: list[dict[str, Any]] = []

    async def infra_checks_with_dev(req: AgentRequest) -> AgentResult:
        # infra → safety execute(deploy_version) → dev verify_deploy_request 를 흉내 낸다
        body = a2a_body(
            "verify_deploy_request",
            _review({**req.input, "tool": "github_actions"}),
            token="mock:safety",
        )
        result = await handlers.verify_deploy_request(AgentRequest.model_validate(body))
        seen.append(result.output)
        return _no_tool(req)

    store = set_deploy_store(DeployStore(infra=Infra(infra_checks_with_dev)))
    dep = asyncio.run(store.request(service, [28], "prod"))
    asyncio.run(store.approve(service, dep.id, "예진"))
    assert seen == [{"approved": True, "reason_code": "deploy_approved", "deployment_id": dep.id}]
