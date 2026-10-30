"""merge_pr 교차검증 근거 모으기 (#104 safety `merge_cross_*` 규칙).

개발 AI PR이 장애 났던 경로(`pr_linked_incident`)에 걸리면, 스스로 판정하지 않고 다른 부서
근거를 모아 `cross_check=true`와 함께 safety에 보낸다. 판정은 safety가 한다.

- `active_incident_overlap`: 총괄 `find_active_incident`를 바뀐 경로의 구성요소마다 묻는다.
  못 모으면 `evidence_missing` +1.
- `data_verdict`: 데이터분석 `anomaly_detection` 최근 24시간 (`normal`·`anomaly`·`unknown`).
  못 모으면 `unknown` + `evidence_missing` +1.
- `recent_incident_count`: planning 상태에서 센다. 이 PR·같은 파일 PR 문단에 걸린 사건 중
  7일 안에 열린 것.

- 근거마다 30초 안에 답이 없으면 못 모은 것으로 센다. 지어내지 않는다.
- 구성요소를 경로로 정하지 못하면 사건 겹침을 물을 수 없으므로 못 모은 근거로 센다.
- 목 모드 기본값(`default_checker`)은 다른 부서를 부르지 않고 `None`을 돌려준다 (예전처럼
  safety 판정만으로 교차검증 대기). 시험은 `caller`를 넣어 실제 흐름을 탄다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError

from ax_agent_template import config as template_config
from ax_agent_template.client import call_agent
from ax_contracts.schemas import (
    TENANT_ID_DEFAULT,
    AgentRequest,
    AgentResult,
    FindActiveIncidentInput,
    FindActiveIncidentOutput,
    RequestMeta,
    TaskStatus,
)
from ax_contracts.schemas.data import AnomalyDetectionInput, AnomalyDetectionOutput
from planning_agent.domain.autocheck import linked_incident_paras
from planning_agent.domain.handover import AREA_BY_PATH
from planning_agent.domain.models import PR, State

EVIDENCE_TIMEOUT_SECONDS = 30.0  # 근거별 대기 (#104 제안값)
DATA_WINDOW = timedelta(hours=24)  # 데이터분석 최근 이상 판정 창
RECENT_INCIDENT_WINDOW = timedelta(days=7)  # 같은 경로 최근 장애 창

# safety thresholds.toml `merge_cross_*` 규칙 키
CROSS_RULES = (
    "merge_cross_active_incident",
    "merge_cross_data_verdict",
    "merge_cross_recent_incident",
    "merge_cross_evidence_missing",
)

AgentCaller = Callable[[str, AgentRequest], Awaitable[AgentResult]]


class EvidenceUnavailable(Exception):
    """근거를 받지 못했다 (꺼짐·거절·시간 초과·계약과 다른 출력)."""


@dataclass
class EvidenceLine:
    """화면에 보여 줄 근거 한 줄. `ok`가 False면 그 근거에 걸렸거나 못 모은 것."""

    source: str  # orchestrator · data · planning
    label: str
    value: str
    ok: bool

    def dump(self) -> dict[str, Any]:
        return {"source": self.source, "label": self.label, "value": self.value, "ok": self.ok}


@dataclass
class CrossEvidence:
    components: list[str]
    active_incident_overlap: int = 0
    data_verdict: str = "unknown"
    recent_incident_count: int = 0
    evidence_missing: int = 0
    lines: list[EvidenceLine] = field(default_factory=list)

    @property
    def params(self) -> dict[str, Any]:
        """`merge_pr` params에 더할 값 (safety `when = { cross_check = true }` 규칙이 본다)."""
        return {
            "cross_check": True,
            "active_incident_overlap": self.active_incident_overlap,
            "data_verdict": self.data_verdict,
            "recent_incident_count": self.recent_incident_count,
            "evidence_missing": self.evidence_missing,
        }

    @property
    def rules(self) -> list[str]:
        """planning 사전 판정: 걸린 `merge_cross_*` 규칙 (최종 판정은 safety)."""
        hit = {
            "merge_cross_active_incident": self.active_incident_overlap > 0,
            "merge_cross_data_verdict": self.data_verdict != "normal",
            "merge_cross_recent_incident": self.recent_incident_count > 0,
            "merge_cross_evidence_missing": self.evidence_missing > 0,
        }
        return [k for k in CROSS_RULES if hit[k]]


def components_of(paths: list[str]) -> list[str]:
    """바뀐 경로 → 구성요소 코드 (총괄 사건 `component`와 같은 값, 경로 단위 비교)."""
    out: list[str] = []
    for path in paths:
        for prefix, area in AREA_BY_PATH:
            if path == prefix or path.startswith(prefix + "/"):
                if area not in out:
                    out.append(area)
                break
    return out


def recent_incident_count(state: State, pr: PR, now: datetime) -> int:
    """이 PR·같은 파일을 바꾼 PR의 문단에 걸린 사건 중 최근 7일 안에 열린 수."""
    ctx_ids = linked_incident_paras(state, pr).values()
    since = now - RECENT_INCIDENT_WINDOW
    return sum(
        1 for inc in state.incidents if inc.context_id in set(ctx_ids) and inc.opened_at >= since
    )


class CrossChecker:
    """다른 부서에 근거를 묻는다. `caller`는 `call_agent`와 같은 모양 (시험에서 바꿔 끼운다)."""

    def __init__(
        self,
        caller: AgentCaller | None = None,
        tenant_id: str = TENANT_ID_DEFAULT,
        timeout: float = EVIDENCE_TIMEOUT_SECONDS,
    ):
        self._call = caller or (lambda target, req: call_agent(target, req))
        self.tenant_id = tenant_id
        self.timeout = timeout

    async def gather(self, state: State, pr: PR, ctx: str, now: datetime) -> CrossEvidence:
        ev = CrossEvidence(components=components_of(pr.changed_paths))
        incidents, data = await asyncio.gather(
            self._incidents(ev.components, ctx),
            self._data(ctx, now),
            return_exceptions=True,
        )

        if isinstance(incidents, BaseException):
            ev.evidence_missing += 1
            ev.lines.append(EvidenceLine("orchestrator", "진행 중인 사건", _why(incidents), False))
        else:
            ev.active_incident_overlap = len(incidents)
            value = ", ".join(incidents) if incidents else f"없음 ({', '.join(ev.components)})"
            ev.lines.append(EvidenceLine("orchestrator", "진행 중인 사건", value, not incidents))

        if isinstance(data, BaseException):
            ev.evidence_missing += 1
            ev.lines.append(EvidenceLine("data", "최근 24시간 이상", _why(data), False))
        else:
            ev.data_verdict, summary = data
            ev.lines.append(
                EvidenceLine("data", "최근 24시간 이상", summary, ev.data_verdict == "normal")
            )

        ev.recent_incident_count = recent_incident_count(state, pr, now)
        ev.lines.append(
            EvidenceLine(
                "planning",
                "같은 경로 최근 7일 장애",
                f"{ev.recent_incident_count}건",
                ev.recent_incident_count == 0,
            )
        )
        return ev

    def _meta(self, ctx: str) -> RequestMeta:
        return RequestMeta(
            context_id=ctx,
            tenant_id=self.tenant_id,
            token="replaced-by-call_agent",
            path=[],
            deadline=datetime.now(UTC) + timedelta(seconds=self.timeout),
        )

    async def _ask(self, target: str, skill: str, ctx: str, inp: dict) -> dict:
        req = AgentRequest(meta=self._meta(ctx), skill=skill, input=inp)
        try:
            out = await asyncio.wait_for(self._call(target, req), self.timeout)
        except TimeoutError as exc:
            raise EvidenceUnavailable(f"{int(self.timeout)}초 안에 응답 없음") from exc
        except Exception as exc:  # 서비스 꺼짐·연결 실패
            raise EvidenceUnavailable(f"호출 실패 ({type(exc).__name__})") from exc
        if out.status != TaskStatus.COMPLETED:
            raise EvidenceUnavailable(f"{out.status.value} {out.outcome or ''}".strip())
        return out.output or {}

    async def _incidents(self, components: list[str], ctx: str) -> list[str]:
        """구성요소마다 열린 사건을 묻고, 겹친 사건 번호를 돌려준다."""
        if not components:
            raise EvidenceUnavailable("바뀐 경로의 구성요소를 정하지 못함")
        found: list[str] = []
        for component in components:
            body = await self._ask(
                "orchestrator",
                "find_active_incident",
                ctx,
                FindActiveIncidentInput(component=component).model_dump(mode="json"),
            )
            try:
                parsed = FindActiveIncidentOutput.model_validate(body)
            except ValidationError as exc:
                raise EvidenceUnavailable("find_active_incident 출력이 계약과 다름") from exc
            if parsed.found and parsed.context_id and parsed.context_id not in found:
                found.append(parsed.context_id)
        return found

    async def _data(self, ctx: str, now: datetime) -> tuple[str, str]:
        inp = AnomalyDetectionInput(window_from=now - DATA_WINDOW, window_to=now)
        body = await self._ask("data", "anomaly_detection", ctx, inp.model_dump(mode="json"))
        try:
            parsed = AnomalyDetectionOutput.model_validate(body)
        except ValidationError as exc:
            raise EvidenceUnavailable("anomaly_detection 출력이 계약과 다름") from exc
        # status가 없는 예전 응답은 any_anomaly로 본다 (#71 인프라와 같은 해석)
        verdict = parsed.status or ("anomaly" if parsed.any_anomaly else "normal")
        if verdict == "normal" and parsed.unknown_metrics:
            verdict = "unknown"
        label = {"normal": "정상", "anomaly": "이상", "unknown": "판정할 수 없음"}[verdict]
        return verdict, f"{label} · {parsed.summary}"[:200]


def _why(exc: BaseException) -> str:
    return f"확인 못 함: {exc}"[:200]


def default_checker() -> CrossChecker | None:
    """목 모드면 다른 부서를 부르지 않는다 (None → 예전처럼 safety 판정만)."""
    if template_config.is_mock("planning"):
        return None
    return CrossChecker()
