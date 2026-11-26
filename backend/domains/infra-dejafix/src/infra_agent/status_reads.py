"""대화방 의뢰용 현황 읽기 skill 2개: `current_incidents`·`latest_deployments` (둘 다 read).

총괄 대화방에서 "지금 장애 있어?"·"배포 상태"를 물으면 총괄이 이 skill을 부른다(11/26).
- 상태를 바꾸지 않는다: `execute()`를 부르지 않고, 롤백·재시작·장애 넣기 빼기는 여기에 없다.
- 회사 범위: 요청의 회사(`meta.tenant_id` → `company_scope`)만 본다. 회사가 없으면 DejaFix를 부르지
  않고(캣북스로 떨어지지 않는다) 연결 전으로 답한다. 다른 회사의 장애·배포는 섞지 않는다.
- 모르는 값을 지어내지 않는다: DejaFix 연결 전이면 `configured: false`, 읽기에 실패하면 실패 결과
  (`dejafix_unavailable`)로 돌려 "장애 없음"으로 읽히지 않게 한다.
- DejaFix·Zabbix가 쓴 글(장애 제목)은 결과에 싣지 않는다(지시로 읽힐 수 있는 글이라 총괄에 넘기지
  않는다). 호스트·심각도·부위 태그만 싣고 길이를 자른다.
- `ref`: 의뢰 카드가 그대로 읽는 짧은 번호("incidents:2"·"incidents:unknown"·"deploys:3").
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic import ValidationError

from ax_agent_template import tool_connections
from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
from ax_contracts.schemas.infra import (
    CurrentIncident,
    CurrentIncidentsInput,
    CurrentIncidentsOutput,
    LatestDeployment,
    LatestDeploymentsInput,
    LatestDeploymentsOutput,
)
from infra_agent import dejafix_watch
from infra_agent.deployments import latest_running
from infra_agent.runtime import get_runtime

logger = logging.getLogger("ax.infra.status_reads")

MAX_INCIDENTS = 20  # 결과에 싣는 장애 수(건수 `count`는 전체)
TEXT_CHARS = 80


def _invalid(request: AgentRequest, exc: ValidationError) -> AgentResult:
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.FAILED,
        outcome="invalid_input",
        output={"message": str(exc)},
    )


def _text(value: Any) -> str | None:
    text = str(value).strip() if value not in (None, "") else ""
    return text[:TEXT_CHARS] or None


def _incident(row: dict[str, Any]) -> CurrentIncident:
    return CurrentIncident(
        id=_text(row.get("id")),
        host=_text((row.get("host") or {}).get("name")),
        severity=_text((row.get("severity") or {}).get("label")),
        started_at=_text(row.get("started_at")),
        component=_text(dejafix_watch.component_of(row)),
    )


def _incidents_result(
    request: AgentRequest, out: CurrentIncidentsOutput, outcome: str, summary: str
) -> AgentResult:
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        outcome=outcome,
        output=out.model_dump(mode="json"),
        evidence=Evidence(
            queries=["GET dejafix /api/incidents?status=problem (읽기 전용)"],
            data_summary=summary,
            collected_at=get_runtime().clock(),
        ),
    )


async def current_incidents(request: AgentRequest) -> AgentResult:
    """지금 요청 회사의 DejaFix 현재 장애(읽기)."""
    try:
        CurrentIncidentsInput.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    not_connected = CurrentIncidentsOutput(configured=False, ref="incidents:unknown")
    try:
        # 보관함 파일 읽기가 이벤트 루프를 막지 않게 한다
        url = await asyncio.to_thread(dejafix_watch.base_url)
        if url is None:
            return _incidents_result(
                request,
                not_connected,
                "not_connected",
                "DejaFix(Zabbix) 연결 전이라 확인할 수 없음",
            )
        rows = await dejafix_watch.fetch_problems(url)
    except (tool_connections.DejafixNoCompany, dejafix_watch.DejafixNotConnected):
        return _incidents_result(
            request, not_connected, "not_connected", "이 회사의 Zabbix 연결 전이라 확인할 수 없음"
        )
    except Exception:  # noqa: BLE001 - 못 읽었다고 "장애 없음"으로 답하지 않는다
        logger.warning("DejaFix 현재 장애를 읽지 못했다", exc_info=True)
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.FAILED,
            outcome="dejafix_unavailable",
            output={"message": "DejaFix에서 현재 장애를 읽지 못했다", "retryable": True},
        )
    rows = sorted(rows, key=lambda r: str(r.get("started_at") or ""), reverse=True)
    out = CurrentIncidentsOutput(
        configured=True,
        count=len(rows),
        incidents=[_incident(r) for r in rows[:MAX_INCIDENTS]],
        ref=f"incidents:{len(rows)}",
    )
    return _incidents_result(
        request,
        out,
        "incidents_found" if rows else "no_incident",
        f"DejaFix 현재 장애 {len(rows)}건",
    )


async def latest_deployments(request: AgentRequest) -> AgentResult:
    """지금 요청 회사의 서비스별 지금 돌고 있는 배포(읽기). 도구에서 받은 성공 배포만."""
    try:
        LatestDeploymentsInput.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    # 요청의 회사 칸만(다른 회사 이력은 섞지 않는다). 회사가 없으면 빈 이력(캣북스로 안 떨어짐)
    history = get_runtime().read_history()
    # live()가 보관함 파일을 읽으므로 이벤트 루프 밖에서 돈다
    rows = await asyncio.to_thread(latest_running, history)
    out = LatestDeploymentsOutput(
        deployments=[
            LatestDeployment(
                service=d.service,
                version=d.version,
                deployed_at=d.deployed_at,
                source=d.source,
                is_rollback=d.is_rollback,
            )
            for d in rows
        ],
        ref=f"deploys:{len(rows)}",
    )
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        outcome="deployments_found" if rows else "no_deployment",
        output=out.model_dump(mode="json"),
        evidence=Evidence(
            queries=[
                "deployments WHERE source IS NOT NULL AND NOT rolled_back (서비스별 최근 1건)"
            ],
            data_summary=f"배포 도구에서 받은 서비스 {len(rows)}개의 최근 배포",
            collected_at=get_runtime().clock(),
            ref_ids=[f"deploy:{d.deploy_id}" for d in rows],
        ),
    )


HANDLERS = {
    "current_incidents": current_incidents,
    "latest_deployments": latest_deployments,
}
