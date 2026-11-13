"""원인 PR: 배포 기록(`deployments.json`)에 원인이 없을 때, 연결된 실제 레포에서 장애 구간 안에
머지된 PR을 원인 후보로 본다. 되돌리기는 그 PR의 되돌리기 PR을 열어 머지하는 것이고, 재배포는
고객사 자동 배포(dev 머지 → 배포)가 맡는다.

- 후보는 개발 AI `find_related_prs`(read, A2A)에 묻는다. 다른 domains 코드는 import하지 않고
  응답은 dict로 읽는다. 레포 정보(`repo`·`connection_id`)가 없는 후보(메모리 PR)는 쓰지 않는다.
- 되돌리기는 개발 AI `revert_merged_pr`(B, 개발이 `execute()`로 관문을 거친다)에 요청한다.
- 진단 결과에서 원인 PR은 서비스 `repo:<연결 id 또는 레포>`, 버전 `pr-<번호>`로 표시한다.
  기존 계약(`SuspectDeployment`)을 바꾸지 않고, 롤백 요청(`service`·`from_version`)으로
  그대로 돌아온다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from ax_agent_template import call_agent
from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta

SERVICE_PREFIX = "repo:"
VERSION_PREFIX = "pr-"
DEV_TIMEOUT_SECONDS = 20.0
REVERT_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True)
class PrSuspect:
    pr_number: int
    title: str
    merged_at: datetime
    repo: str | None
    connection_id: str | None
    merge_sha: str | None = None

    @property
    def service(self) -> str:
        return f"{SERVICE_PREFIX}{self.connection_id or self.repo}"

    @property
    def version(self) -> str:
        return f"{VERSION_PREFIX}{self.pr_number}"


def is_pr_service(service: str) -> bool:
    return service.startswith(SERVICE_PREFIX)


def _time(value: Any) -> datetime | None:
    try:
        at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def parse_candidates(output: dict[str, Any] | None) -> list[PrSuspect]:
    """`find_related_prs` 출력 → 원인 PR 후보 (최근 머지 먼저). 레포 정보가 없으면 뺀다."""
    out: list[PrSuspect] = []
    for row in (output or {}).get("candidates") or []:
        if not isinstance(row, dict):
            continue
        repo, conn = row.get("repo"), row.get("connection_id")
        number, merged_at = row.get("pr_number"), _time(row.get("merged_at"))
        if not (repo or conn) or not isinstance(number, int) or merged_at is None:
            continue
        out.append(
            PrSuspect(
                pr_number=number,
                title=str(row.get("title") or ""),
                merged_at=merged_at,
                repo=repo,
                connection_id=conn,
                merge_sha=row.get("merge_sha"),
            )
        )
    return sorted(out, key=lambda s: s.merged_at, reverse=True)


class DevClient(Protocol):
    async def related_prs(
        self, meta: RequestMeta, component: str | None, since: datetime, now: datetime
    ) -> list[PrSuspect]: ...

    async def revert_pr(
        self, meta: RequestMeta, suspect: PrSuspect, reason: str
    ) -> AgentResult: ...


def _meta(meta: RequestMeta, seconds: float, key: str | None) -> RequestMeta:
    deadline = min(meta.deadline, datetime.now(UTC) + timedelta(seconds=seconds))
    return meta.model_copy(update={"deadline": deadline, "idempotency_key": key})


class A2ADev:
    """개발 AI를 A2A로 부른다 (같은 사건 context_id)."""

    async def related_prs(
        self, meta: RequestMeta, component: str | None, since: datetime, now: datetime
    ) -> list[PrSuspect]:
        request = AgentRequest(
            meta=_meta(meta, DEV_TIMEOUT_SECONDS, None),
            skill="find_related_prs",
            input={
                "window_from": since.isoformat(),
                "window_to": now.isoformat(),
                "component": component,
            },
        )
        result = await call_agent("dev", request)
        return parse_candidates(result.output if isinstance(result.output, dict) else None)

    async def revert_pr(self, meta: RequestMeta, suspect: PrSuspect, reason: str) -> AgentResult:
        key = f"{meta.context_id}:revert_merged_pr:{suspect.service}:{suspect.pr_number}"
        request = AgentRequest(
            meta=_meta(meta, REVERT_TIMEOUT_SECONDS, key),
            skill="revert_merged_pr",
            input={
                "connection_id": suspect.connection_id or "",
                "pr_number": suspect.pr_number,
                "reason": reason,
                "incident_context_id": meta.context_id,
            },
        )
        return await call_agent("dev", request)


class NoDev:
    """목 모드 기본값: 실제 레포 후보가 없다(기존 배포 기록 진단만 쓴다)."""

    async def related_prs(
        self, meta: RequestMeta, component: str | None, since: datetime, now: datetime
    ) -> list[PrSuspect]:
        return []

    async def revert_pr(self, meta: RequestMeta, suspect: PrSuspect, reason: str) -> AgentResult:
        raise RuntimeError("개발 AI 연결이 없다")
