"""배포 후 확인 신호원. 작은 인터페이스 뒤에 두고 목 기본값을 쓴다.

- DejaFix 장애: `GET {PLANNING_DEJAFIX_API_URL}/api/incidents?status=problem` (읽기).
  응답 필드 이름이 정해지지 않아 여러 이름을 받는다. 못 읽으면 `unavailable` 신호를 남긴다.
- data `anomaly_detection`(read): 목 모드가 아닐 때만 부른다.
- 목 신호원: 시드 사건(ctx_inc_0001)과 같은 고정 신호를 돌려준다.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx
from pydantic import ValidationError

from ax_agent_template import config as template_config
from ax_agent_template.client import call_agent
from ax_contracts.schemas import (
    TENANT_ID_DEFAULT,
    AgentRequest,
    AgentResult,
    RequestMeta,
    TaskStatus,
)
from ax_contracts.schemas.data import AnomalyDetectionInput, AnomalyDetectionOutput
from planning_agent.domain.deploy import Deployment, Signal
from planning_agent.domain.models import Incident

DEJAFIX_URL_ENV = "PLANNING_DEJAFIX_API_URL"
CALL_TIMEOUT_SECONDS = 5.0

_TIME_KEYS = (
    "opened_at",
    "openedAt",
    "started_at",
    "startedAt",
    "start_time",
    "startTime",
    "created_at",
    "createdAt",
    "detected_at",
    "time",
)
_TITLE_KEYS = ("title", "name", "summary", "description", "message")
_ID_KEYS = ("context_id", "id", "incident_id", "incidentId", "key")
_LIST_KEYS = ("data", "items", "incidents", "results")


class SignalUnavailable(Exception):
    """신호원을 읽지 못했다."""


class SignalSource(Protocol):
    async def signals(
        self, dep: Deployment, window_from: datetime, window_to: datetime
    ) -> list[Signal]: ...


# ---------------------------------------------------------------- DejaFix


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = value / 1000 if value > 1e11 else value  # 밀리초도 받는다
        return datetime.fromtimestamp(seconds, UTC)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)  # 시간대 없는 값은 UTC로 본다
    return parsed.astimezone(UTC)


def _first(item: dict, keys: tuple[str, ...]) -> Any:
    for key in keys:
        if item.get(key) not in (None, ""):
            return item[key]
    return None


def parse_incidents(body: Any) -> list[Signal]:
    """DejaFix 응답을 장애 신호로 바꾼다. 목록이 아니면 `SignalUnavailable`."""
    rows = body
    if isinstance(body, dict):
        rows = next((body[k] for k in _LIST_KEYS if isinstance(body.get(k), list)), None)
    if not isinstance(rows, list):
        raise SignalUnavailable("목록이 아니다")
    out: list[Signal] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        ref = _first(item, _ID_KEYS)
        title = _first(item, _TITLE_KEYS)
        out.append(
            Signal(
                kind="incident",
                source="dejafix",
                ref=str(ref) if ref is not None else None,
                title=str(title) if title is not None else "",
                at=_parse_time(_first(item, _TIME_KEYS)),
            )
        )
    return out


class DejaFixIncidentSource:
    """DejaFix 열린 장애 목록. `client`는 테스트에서 바꾼다."""

    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self._client = client

    async def signals(
        self, dep: Deployment, window_from: datetime, window_to: datetime
    ) -> list[Signal]:
        client = self._client or httpx.AsyncClient()
        try:
            res = await client.get(
                f"{self.base_url}/api/incidents",
                params={"status": "problem"},
                timeout=CALL_TIMEOUT_SECONDS,
            )
            res.raise_for_status()
            body = res.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SignalUnavailable(str(exc)) from exc
        finally:
            if self._client is None:
                await client.aclose()
        return parse_incidents(body)


# ---------------------------------------------------------------- data anomaly_detection

AgentCaller = Callable[[str, AgentRequest], Awaitable[AgentResult]]


class DataAnomalySource:
    """data `anomaly_detection`. 목 모드에서는 부르지 않는다."""

    def __init__(self, caller: AgentCaller | None = None, tenant_id: str = TENANT_ID_DEFAULT):
        self._call = caller or (lambda target, req: call_agent(target, req))
        self.tenant_id = tenant_id

    async def signals(
        self, dep: Deployment, window_from: datetime, window_to: datetime
    ) -> list[Signal]:
        if template_config.is_mock("planning"):
            return []
        meta = RequestMeta(
            context_id=f"ctx_dep_{dep.id.lower()}",
            tenant_id=self.tenant_id,
            token="replaced-by-call_agent",
            path=[],
            deadline=datetime.now(UTC) + timedelta(seconds=CALL_TIMEOUT_SECONDS),
        )
        inp = AnomalyDetectionInput(window_from=window_from, window_to=window_to)
        out = await self._call(
            "data",
            AgentRequest(meta=meta, skill="anomaly_detection", input=inp.model_dump(mode="json")),
        )
        if out.status != TaskStatus.COMPLETED:
            raise SignalUnavailable(f"{out.status.value} {out.outcome or ''}".strip())
        try:
            parsed = AnomalyDetectionOutput.model_validate(out.output)
        except ValidationError as exc:
            raise SignalUnavailable("anomaly_detection 출력이 계약과 다르다") from exc
        return [
            Signal(
                kind="anomaly",
                source="data",
                ref=v.metric,
                title=f"{v.metric} {v.ratio}배" if v.ratio is not None else v.metric,
                severity=v.severity,
            )
            for v in parsed.verdicts
            if v.is_anomaly
        ]


# ---------------------------------------------------------------- 목·묶음


class MockSignalSource:
    """시드 사건을 장애 신호로, 고정 이상 지표 하나를 같이 돌려준다 (결정적)."""

    def __init__(self, incidents: list[Incident], anomalies: list[Signal] | None = None):
        self._incidents = incidents
        self._anomalies = anomalies or []

    async def signals(
        self, dep: Deployment, window_from: datetime, window_to: datetime
    ) -> list[Signal]:
        return mock_signals(self._incidents, self._anomalies)


def mock_signals(incidents: list[Incident], anomalies: list[Signal]) -> list[Signal]:
    rows = [
        Signal(kind="incident", source="mock", ref=i.context_id, title=i.title, at=i.opened_at)
        for i in incidents
    ]
    return [*rows, *anomalies]


class CombinedSource:
    """여러 신호원을 차례로 읽는다. 못 읽은 곳은 `unavailable` 신호로 남긴다."""

    def __init__(self, sources: list[tuple[str, SignalSource]]):
        self._sources = sources

    async def signals(
        self, dep: Deployment, window_from: datetime, window_to: datetime
    ) -> list[Signal]:
        out: list[Signal] = []
        for name, src in self._sources:
            try:
                out.extend(await src.signals(dep, window_from, window_to))
            except SignalUnavailable as exc:
                out.append(Signal(kind="unavailable", source=name, title=str(exc)[:200]))
        return out


def default_source(mock: SignalSource) -> SignalSource:
    """목 모드이고 DejaFix 주소가 없으면 목 신호원. 아니면 DejaFix + data."""
    url = os.getenv(DEJAFIX_URL_ENV, "").strip()
    if template_config.is_mock("planning") and not url:
        return mock
    sources: list[tuple[str, SignalSource]] = []
    if url:
        sources.append(("dejafix", DejaFixIncidentSource(url)))
    else:
        sources.append(("dejafix", _Missing(f"{DEJAFIX_URL_ENV} 없음")))
    sources.append(("data", DataAnomalySource()))
    return CombinedSource(sources)


class _Missing:
    def __init__(self, why: str):
        self._why = why

    async def signals(
        self, dep: Deployment, window_from: datetime, window_to: datetime
    ) -> list[Signal]:
        raise SignalUnavailable(self._why)
