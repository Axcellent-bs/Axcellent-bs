"""승인된 개발 배포를 연결된 배포 도구로 띄운다.

`deploy_version`(상태 변경), `deploy_run_status`·`deploy_tool_services`(read).

- dev 배포 기록(DEP-n)을 사람이 승인하면 dev가 A2A로 부른다. `approved_by`(승인한 사람)가
  비었으면 띄우지 않는다. 도구 연결의 배포 도구는 환경 구분이 없어 운영 배포로 본다.
- 배포 도구는 도구 연결(공용 보관함)의 서비스별 하나(`deploy_tools.tool_for`). 연결이 없으면
  관문을 부르기 전에 `no_deploy_tool` 실패로 돌려준다. 성공을 지어내지 않는다.
- 도구를 부르기 전에 반드시 `execute(deploy_version)`를 거친다. 멱등 키는 dev 배포 기록 id(고유
  꼬리 포함)·서비스·버전으로 만든다 (`deploy:<request_id>:deploy_version:<service>:<version>`).
  같은 키로 결과도 남긴다.
- 한 번만: 관문이 `executed`를 준 뒤 도구를 한 번이라도 불렀으면(성공·실패·중단 모두) 그 결과를 남겨
  같은 키로 다시 와도 도구를 다시 부르지 않는다. 같은 키 요청은 한 번에 하나씩만 처리한다.
  보류(held)·관문 거절은 도구를 부르지 않았으니 남기지 않는다
  (승인 뒤 같은 요청을 다시 보내면 실행).
- 도구 호출이 시간 초과면 `deploy_tools`가 요청 직전 이후 같은 버전 실행을 찾아 본다
  (두 번 띄우지 않게).
  그래도 모르면 실패 + "도구에서 먼저 확인" 안내이고 저절로 다시 보내지 않는다.
- 기록·잠금은 프로세스 메모리에만 있다 (infra 서버는 워커 하나로 돈다고 본다: 여러 프로세스로 띄우면
  같은 요청을 막지 못한다). 재시작 뒤 같은 요청을 다시 보내지 않는 책임은 부른 쪽(dev 배포 기록이
  "보내는 중"을 파일에 먼저 남긴다)에 있다.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import ValidationError

from ax_agent_template.execute import execute
from ax_agent_template.tool_connections import ToolConnectionError
from ax_contracts.schemas import (
    AgentRequest,
    AgentResult,
    Evidence,
    ExecuteDecision,
    InputReason,
    TaskStatus,
)
from ax_contracts.schemas.infra import (
    DeployRunState,
    DeployRunStatusInput,
    DeployRunStatusOutput,
    DeployToolService,
    DeployToolServicesInput,
    DeployToolServicesOutput,
    DeployVersionInput,
    DeployVersionOutput,
)
from infra_agent import deploy_tools
from infra_agent.deploy_tools import DeployTool, ToolRun
from infra_agent.runtime import get_runtime

ACTION = "deploy_version"
RECENT_LIMIT = 30
NO_TOOL = "배포 도구가 연결되지 않았어요 — 도구 연결에서 설정"
CHECK_FIRST = "먼저 배포 도구에서 이 실행이 이미 시작됐는지 확인하세요 (중복 실행 방지)"


def _failed(request: AgentRequest, outcome: str, output: dict[str, Any]) -> AgentResult:
    return AgentResult(meta=request.meta, status=TaskStatus.FAILED, outcome=outcome, output=output)


def idempotency_key(inp: DeployVersionInput) -> str:
    return f"deploy:{inp.request_id}:{ACTION}:{inp.service}:{inp.version}"


def _base(inp: DeployVersionInput) -> dict[str, Any]:
    return {"service": inp.service, "version": inp.version, "request_id": inp.request_id}


async def deploy_version(request: AgentRequest) -> AgentResult:
    try:
        inp = DeployVersionInput.model_validate(request.input)
    except ValidationError as exc:
        return _failed(request, "invalid_input", {"message": str(exc)})
    if not (inp.approved_by or "").strip():
        # 배포 실행은 사람 승인이 꼭 있어야 한다 (운영 배포 도구)
        return _failed(
            request,
            "approval_required",
            {**_base(inp), "message": "승인한 사람이 없어 배포하지 않았다", "retryable": False},
        )
    rt = get_runtime()
    key = idempotency_key(inp)
    if (done := rt.deploys.get(key)) is not None:
        return done.model_copy(update={"meta": request.meta})
    async with rt.deploy_locks.hold(key):
        if (done := rt.deploys.get(key)) is not None:
            return done.model_copy(update={"meta": request.meta})
        return await _deploy_once(request, rt, inp, key)


async def _deploy_once(
    request: AgentRequest, rt: Any, inp: DeployVersionInput, key: str
) -> AgentResult:
    base = _base(inp)
    # 보관함 파일 읽기가 이벤트 루프를 막지 않게 한다
    tool = await asyncio.to_thread(deploy_tools.tool_for, inp.service)
    if tool is None:
        return _failed(
            request,
            "no_deploy_tool",
            {**base, "message": f"{inp.service}: {NO_TOOL}", "retryable": False},
        )
    evidence = Evidence(
        queries=[f"tool_connections WHERE kind={tool.tool} AND config.service={inp.service}"],
        data_summary=(
            f"{inp.service} {inp.version} 운영 배포 도구로 실행 ({tool.tool}) · "
            f"배포 {inp.request_id} · 요청 {inp.requester or '-'} · 승인 {inp.approved_by}"
        ),
        collected_at=rt.clock(),
        ref_ids=[f"deploy_request:{inp.request_id}"],
    )
    params = {**inp.model_dump(mode="json"), "tool": tool.tool}
    decision = await execute(
        ACTION, params, request.meta.model_copy(update={"idempotency_key": key}), evidence
    )
    if decision.status != "executed":
        return _not_executed(request, decision, base, evidence)
    try:
        result = await _dispatch(request, tool, inp, decision, evidence)
    except BaseException:
        # 취소·예상 못 한 오류로 중간에 멈췄다.
        # 배포가 이미 시작됐을 수 있으니 다시 부르지 않게 남긴다
        rt.deploys[key] = _failed(
            request,
            "deploy_failed",
            {
                **base,
                "tool": tool.tool,
                "message": "배포 요청이 중간에 멈췄어요. 배포가 시작됐을 수 있어요",
                "handoff_steps": [CHECK_FIRST],
                "retryable": False,
            },
        )
        raise
    rt.deploys[key] = result
    return result


async def _dispatch(
    request: AgentRequest,
    tool: DeployTool,
    inp: DeployVersionInput,
    decision: ExecuteDecision,
    evidence: Evidence,
) -> AgentResult:
    base = _base(inp)
    try:
        ref = await tool.redeploy(inp.version)
    except ToolConnectionError as exc:  # 문구에 비밀 값이 없다
        steps = [tool.describe(inp.version)]
        if exc.code == "timeout":
            steps.insert(0, CHECK_FIRST)
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.FAILED,
            outcome="deploy_failed",
            output={
                **base,
                "tool": tool.tool,
                "code": exc.code,
                "message": f"배포 도구 호출 실패: {exc.message}",
                "handoff_steps": steps,
                # 다시 부르면 배포가 두 번 돌 수 있다
                "retryable": False,
                "execution_id": decision.execution_id,
            },
            evidence=evidence,
        )
    except Exception as exc:  # noqa: BLE001 - 예외 문구는 넣지 않는다(바깥 응답이 섞일 수 있다)
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.FAILED,
            outcome="deploy_failed",
            output={
                **base,
                "tool": tool.tool,
                "message": f"배포 도구 호출 실패: {type(exc).__name__}",
                "handoff_steps": [CHECK_FIRST, tool.describe(inp.version)],
                "retryable": False,
                "execution_id": decision.execution_id,
            },
            evidence=evidence,
        )
    out = DeployVersionOutput(
        **base,
        tool=ref.tool,
        run_id=ref.run_id,
        url=ref.url,
        status="dispatched",
        execution_id=decision.execution_id,
        grade=decision.grade,
    )
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        evidence=evidence,
        decision_reason=evidence.data_summary,
    )


def _not_executed(
    request: AgentRequest, decision: ExecuteDecision, base: dict[str, Any], evidence: Evidence
) -> AgentResult:
    hold = decision.hold_reason.value if decision.hold_reason else None
    common = {**base, "execution_id": decision.execution_id, "grade": decision.grade}
    if decision.status == "held":
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.INPUT_REQUIRED,
            reason_code=InputReason.HUMAN_INPUT,
            outcome="held",
            output={
                **common,
                "review_id": decision.review_id,
                "hold_reason": hold,
                "message": "안전 확인에서 사람 승인 대기. 승인 뒤 같은 요청을 다시 보내면 실행한다",
            },
            evidence=evidence,
        )
    if decision.status == "rejected" and decision.execution_id != "exe_unavailable":
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.COMPLETED,
            outcome="rejected",
            output={**common, "hold_reason": hold, "message": "안전 관문이 거절해 배포하지 않았다"},
            evidence=evidence,
        )
    outcome = {
        "rejected": "safety_unavailable",
        "verifying": "verification_pending",
        "failed": "execute_failed",
    }.get(decision.status, "execute_failed")
    return _failed(request, outcome, {**common, "message": "배포하지 않았다 (fail-closed)"})


# ---------- deploy_run_status (read) ----------


def run_state(run: ToolRun | None) -> DeployRunState:
    if run is None:
        return "unknown"
    if run.finished is False:
        return "running"
    if run.ok:
        return "succeeded"
    return "failed" if run.finished else "unknown"


async def deploy_run_status(request: AgentRequest) -> AgentResult:
    """도구의 최근 실행에서 그 실행 번호를 찾아 상태를 돌려준다. 못 찾으면 unknown."""
    try:
        inp = DeployRunStatusInput.model_validate(request.input)
    except ValidationError as exc:
        return _failed(request, "invalid_input", {"message": str(exc)})
    rt = get_runtime()
    tool = await asyncio.to_thread(deploy_tools.tool_for, inp.service)
    if tool is None:
        return _failed(request, "no_deploy_tool", {"service": inp.service, "message": NO_TOOL})
    try:
        run_id, resolving = await _resolve(tool, inp.run_id)
        runs = await tool.recent(RECENT_LIMIT)
    except ToolConnectionError as exc:
        return _failed(
            request,
            "tool_unavailable",
            {"service": inp.service, "code": exc.code, "message": exc.message},
        )
    except Exception as exc:  # noqa: BLE001 - 예외 문구는 넣지 않는다
        return _failed(
            request, "tool_unavailable", {"service": inp.service, "message": type(exc).__name__}
        )
    run = next((r for r in runs if r.run_id == run_id), None)
    out = DeployRunStatusOutput(
        service=inp.service,
        run_id=run_id,
        tool=tool.tool,
        status=run_state(run),
        url=run.url if run else None,
        checked_at=rt.clock(),
        resolving=resolving,
    )
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        evidence=Evidence(
            queries=[f"{tool.tool} recent runs LIMIT {RECENT_LIMIT} WHERE run_id={inp.run_id}"],
            data_summary=f"{inp.service} 실행 {inp.run_id}: {out.status}",
            collected_at=out.checked_at,
        ),
    )


async def _resolve(tool: DeployTool, run_id: str) -> tuple[str, bool]:
    """Jenkins 대기열 id(`queue-N`)를 빌드 번호로. 아직 모르면 (그대로, True: 도구에서 확인 중)."""
    resolve = getattr(tool, "resolve_run", None)
    if resolve is None or not run_id.startswith("queue-"):
        return run_id, False
    number = await resolve(run_id)
    return (number, False) if number else (run_id, True)


# ---------- deploy_tool_services (read) ----------


async def deploy_tool_services(request: AgentRequest) -> AgentResult:
    """배포 도구가 연결된 서비스 목록 (서비스마다 도구 하나). 보관함이 없으면 빈 목록."""
    try:
        DeployToolServicesInput.model_validate(request.input)
    except ValidationError as exc:
        return _failed(request, "invalid_input", {"message": str(exc)})
    tools = await asyncio.to_thread(deploy_tools.all_tools)
    out = DeployToolServicesOutput(
        services=[DeployToolService(service=t.service, tool=t.tool) for t in tools]
    )
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        evidence=Evidence(
            queries=["tool_connections WHERE kind IN (github_actions, jenkins)"],
            data_summary=f"배포 도구 연결 {len(out.services)}개",
            collected_at=get_runtime().clock(),
        ),
    )


HANDLERS = {
    "deploy_version": deploy_version,
    "deploy_run_status": deploy_run_status,
    "deploy_tool_services": deploy_tool_services,
}
