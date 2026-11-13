"""원천 데이터 조회. 재무는 요청 쪽 숫자를 믿지 않고 여기서 받은 값으로 다시 계산한다.

- 목 모드(`FINANCE_MOCK=1` 또는 `AGENT_TEMPLATE_MOCK=1`): 패키지 안 `mock_seed.json`
- 실제: data 에이전트(`impact_analysis`, `fraud_score`)를 A2A로 부르고, 결제 금액은 shop-api
  주문 조회(`GET /api/shop/orders/{order_id}`)의 성공 결제 합계로 본다.
  판매 사이트 DB에는 붙지 않는다.

못 받은 값은 None이고, 호출한 쪽은 None을 승인 근거로 쓰지 않는다(fail-closed).
data `impact_analysis`의 입력·출력은 계약(`ImpactAnalysisInput`·`ImpactAnalysisOutput`)으로
만들고 확인한다.
장애 구간(`window_from`·`window_to`)을 모르면 data에 묻지 않는다.

팀 예산의 팀별 인원수는 인사 AI `team_headcount`(계약 `TeamHeadcountOutput`)를 A2A로 묻는다.
이름·직원 ID는 받지 않는다. 못 받으면 None이고 예산을 0원으로 지어내지 않는다.

장애 구간은 총괄 사건 목록(`GET /api/incidents`)을 읽기 전용으로 읽어서 안다
(계약 `data_access/orchestrator_incidents.py`, [협업] #417 제안 A, 요청 #418).
사건 **중**에 총괄을 부르는 게 아니다 — 평상시 사건 조회에서만 읽는다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from importlib import resources
from typing import Any, Protocol

import httpx
from pydantic import ValidationError

from ax_agent_template import call_agent
from ax_agent_template.config import agent_url
from ax_agent_template.services import route_target
from ax_contracts.data_access.orchestrator_incidents import OrchestratorIncident
from ax_contracts.schemas import (
    AgentRequest,
    ImpactAnalysisInput,
    ImpactAnalysisOutput,
    RequestMeta,
    TaskStatus,
    TeamHeadcountInput,
    TeamHeadcountOutput,
)

DATA_CALL_SECONDS = 10.0
SHOP_CALL_SECONDS = 5.0
ORCHESTRATOR_CALL_SECONDS = 5.0
# 총괄 사건 행에서 재무가 읽는 필드 ([협업] #417에서 합의한 다섯 개). 나머지 키는 보지 않는다
INCIDENT_FIELDS = (
    "context_id",
    "component",
    "status",
    "opened_at",
    "closed_at",
    "impact_from",
    "impact_to",
    "recovered_at",
)


def order_key(order_id: object) -> str | None:
    """주문 번호를 문자열로 맞춘다. 고객지원은 숫자, shop-api는 `ord_` 문자열을 쓴다."""
    if isinstance(order_id, bool):
        return None
    if isinstance(order_id, int) and order_id > 0:
        return str(order_id)
    if isinstance(order_id, str) and order_id.strip():
        return order_id.strip()
    return None


IMPACT_FIELDS = ("failed_order_count", "affected_customer_count", "damage_amount")


@dataclass(frozen=True)
class Impact:
    failed_orders: int
    affected_customers: int
    lost_amount: int
    affected_customer_ids: frozenset[str] = field(default_factory=frozenset)
    ref: str = ""
    by_category: tuple[tuple[str, int, int], ...] = ()  # (category, 실패 주문 수, 피해 금액)
    # 구간이 총괄이 영향 분석에 쓴 구간과 같은지. False면 AI 처리 시각으로 대신한 근사 구간이라
    # 영향 고객 목록으로 "보상 대상 밖"을 판정하지 않는다
    window_exact: bool = True


class Sources(Protocol):
    async def impact(
        self,
        meta: RequestMeta,
        context_id: str,
        window: dict[str, Any] | None,
        *,
        final: bool = False,
    ) -> Impact | None: ...

    async def incident_window(self, context_id: str) -> dict[str, Any] | None: ...

    async def team_headcount(self, meta: RequestMeta, month: str) -> TeamHeadcountOutput | None: ...

    def forget_headcount(self, month: str) -> None: ...


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _impact_from(output: dict[str, Any], ref: str) -> Impact | None:
    # data `ImpactCounts` 필드 이름 (agents/data impact.py, DEP-DATA-02)
    counts = [_int(output.get(k)) for k in IMPACT_FIELDS]
    if any(c is None for c in counts):
        return None
    ids = output.get("affected_customer_ids") or []
    by_category = []
    for row in output.get("by_category") or []:
        if not isinstance(row, dict) or not isinstance(row.get("category"), str):
            continue
        orders, amount = _int(row.get("failed_order_count")), _int(row.get("damage_amount"))
        if orders is not None and amount is not None:
            by_category.append((row["category"], orders, amount))
    return Impact(
        failed_orders=counts[0],  # type: ignore[arg-type]
        affected_customers=counts[1],  # type: ignore[arg-type]
        lost_amount=counts[2],  # type: ignore[arg-type]
        affected_customer_ids=frozenset(i for i in ids if isinstance(i, str)),
        ref=ref,
        by_category=tuple(by_category),
    )


def impact_input(window: dict[str, Any] | None) -> ImpactAnalysisInput | None:
    """재무가 아는 사건 구간 → data `impact_analysis` 입력. 구간이 없거나 틀리면 None."""
    if not window:
        return None
    try:
        return ImpactAnalysisInput.model_validate(
            {k: window.get(k) for k in ("window_from", "window_to", "category") if window.get(k)}
        )
    except ValidationError:
        return None


class MockSources:
    """시드 데이터. 단독 테스트와 CI에서 쓴다."""

    def __init__(self, seed: dict[str, Any] | None = None) -> None:
        if seed is None:
            text = resources.files("finance_agent").joinpath("mock_seed.json").read_text("utf-8")
            seed = json.loads(text)
        self.seed = seed

    async def impact(
        self,
        meta: RequestMeta,
        context_id: str,
        window: dict[str, Any] | None,
        *,
        final: bool = False,
    ) -> Impact | None:
        row = self.seed.get("impacts", {}).get(context_id)
        return _impact_from(row, f"mock:impact:{context_id}") if row else None

    async def incident_window(self, context_id: str) -> dict[str, Any] | None:
        """목 모드는 장부 시드(`seed_incidents`)로 구간을 알아서 총괄을 읽지 않는다."""
        return None

    async def team_headcount(self, meta: RequestMeta, month: str) -> TeamHeadcountOutput | None:
        """시드의 월별 팀 인원. 그 달이 없으면 그 전 가장 가까운 달(발령이 없던 달)을 쓴다."""
        months = self.seed.get("team_headcount") or {}
        known = sorted(m for m in months if m[:1].isdigit() and m <= month)
        if not known:
            return None
        return TeamHeadcountOutput.model_validate(
            {
                "month": month,
                "rows": [{"team": t, "headcount": n} for t, n in months[known[-1]].items()],
                "basis": "목 시드(테스트용)",
                "collected_at": datetime.now(UTC),
            }
        )

    def forget_headcount(self, month: str) -> None:
        """목 시드는 기억해 둔 값이 없다."""


# 팀 인원은 하루에 몇 번 바뀌지 않는다. 재무 화면이 몇 초마다 불러도 인사에 다시 묻지 않는다
HEADCOUNT_CACHE_SECONDS = 300
HEADCOUNT_FAILED_SECONDS = 60
HR_CALL_SECONDS = 10.0

# 같은 사건·같은 구간의 영향 분석은 이 시간 동안 다시 묻지 않는다. 재무 화면이 몇 초마다 다시 불러도
# data에 같은 A2A 요청이 반복되지 않게 한다 (AI 대화방에 같은 대화가 쌓이지 않게)
IMPACT_CACHE_SECONDS = 300

impact_log = logging.getLogger("finance.impact")


def _started_at(output: dict[str, Any] | None) -> datetime | None:
    """데이터분석이 처리를 시작한 시각(응답 `started_at`). 없거나 읽을 수 없으면 None."""
    raw = (output or {}).get("started_at")
    if not isinstance(raw, str):
        return None
    try:
        at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo is not None else None


def _ms(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    return round((end - start).total_seconds() * 1000)


def _log_impact(context_id: str, cache: str, **fields: Any) -> None:
    """영향 분석 요청 로그 한 줄 (JSON).

    cache: hit(기억한 값) · inflight(묻는 중인 요청을 같이 기다림) · miss(새로 물음).
    miss는 queue_ms·process_ms·total_ms를 같이 남긴다 (처리 시작 시각을 모르면 null).
    """
    line = {"event": "impact_analysis", "context_id": context_id, "cache": cache, **fields}
    impact_log.info(json.dumps(line, ensure_ascii=False))


class LiveSources:
    """data 에이전트 A2A 호출. 응답이 없거나 형식이 다르면 None."""

    async def _ask_data(
        self, meta: RequestMeta, skill: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        deadline = min(meta.deadline, datetime.now(UTC) + timedelta(seconds=DATA_CALL_SECONDS))
        request = AgentRequest(
            meta=meta.model_copy(update={"deadline": deadline}), skill=skill, input=payload
        )
        result = await call_agent("data", request)
        return result.output if result.status == TaskStatus.COMPLETED else None

    async def impact(
        self,
        meta: RequestMeta,
        context_id: str,
        window: dict[str, Any] | None,
        *,
        final: bool = False,
    ) -> Impact | None:
        """사건 구간의 영향. 구간을 모르면 묻지 않고 None (data 계약은 구간이 필수다).

        사건 번호는 입력이 아니라 요청 meta(`context_id`)로 전달된다.
        `final`(해결로 닫힌 사건)이면 한 번 받은 결과를 계속 쓴다
        (프로세스 메모리라 다시 켜면 한 번 다시 묻는다).
        진행 중·degraded 사건은 IMPACT_CACHE_SECONDS마다 다시 묻는다.
        같은 사건·구간을 이미 묻는 중이면 새로 묻지 않고 그 결과를 같이 기다린다
        (진행 중 요청 합치기).
        그 요청이 예외로 끝나면 기다리던 쪽도 같은 예외를 받는다. 실패(None·예외)는 기억하지 않는다.
        """
        payload = impact_input(window)
        if payload is None:
            return None
        body = payload.model_dump(mode="json", exclude_none=True)
        key = (context_id, json.dumps(body, sort_keys=True))
        hit = self._impact_cache.get(key)
        if hit is not None and (hit[2] or time.monotonic() - hit[0] < IMPACT_CACHE_SECONDS):
            _log_impact(context_id, "hit")
            return hit[1]  # 닫힌 사건이면 시간과 상관없이, 아니면 5분 안
        pending = self._impact_inflight.get(key)
        if pending is not None:
            _log_impact(context_id, "inflight")
            return await asyncio.shield(pending)  # 기다리던 쪽이 취소돼도 묻는 요청은 계속
        future: asyncio.Future[Impact | None] = asyncio.get_running_loop().create_future()
        self._impact_inflight[key] = future
        try:
            impact = await self._fetch_impact(meta, context_id, body)
        except asyncio.CancelledError:
            future.cancel()
            raise
        except Exception as exc:
            future.set_exception(exc)
            future.exception()  # 기다리는 쪽이 없어도 "꺼내지 않은 예외" 경고가 나지 않게
            raise
        else:
            future.set_result(impact)
            if impact is not None:  # 실패는 기억하지 않는다 (다음 호출이 다시 묻는다)
                self._impact_cache[key] = (time.monotonic(), impact, final)
            return impact
        finally:
            self._impact_inflight.pop(key, None)

    async def _fetch_impact(
        self, meta: RequestMeta, context_id: str, body: dict[str, Any]
    ) -> Impact | None:
        """데이터분석에 한 번 묻는다. 대기(요청~처리 시작)·처리(처리 시작~응답) 시간을 남긴다."""
        sent = datetime.now(UTC)
        output = await self._ask_data(meta, "impact_analysis", body)
        received = datetime.now(UTC)
        started = _started_at(output)
        # 각각 반올림하면 합이 1ms 어긋날 수 있어 처리 시간은 전체에서 대기를 뺀다
        total_ms = _ms(sent, received)
        queue_ms = _ms(sent, started)
        process_ms = None if queue_ms is None or total_ms is None else total_ms - queue_ms
        _log_impact(
            context_id,
            "miss",
            queue_ms=queue_ms,
            process_ms=process_ms,
            total_ms=total_ms,
            ok=output is not None,
        )
        if output is None:
            return None
        try:
            ImpactAnalysisOutput.model_validate(output)
        except ValidationError:
            return None
        return _impact_from(output, f"data:impact_analysis:{context_id}")

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._http = http_client  # 테스트용
        # (사건, 구간) → (받은 시각, 영향, 닫힌 사건인지)
        self._impact_cache: dict[tuple[str, str], tuple[float, Impact | None, bool]] = {}
        # (사건, 구간) → 지금 데이터분석에 묻는 중인 요청의 결과
        self._impact_inflight: dict[tuple[str, str], asyncio.Future[Impact | None]] = {}
        self._headcount_cache: dict[str, tuple[float, TeamHeadcountOutput | None]] = {}

    async def team_headcount(self, meta: RequestMeta, month: str) -> TeamHeadcountOutput | None:
        """인사 `team_headcount` (읽기). 응답이 없거나 계약과 다르면 None."""
        # 인사에 아직 이 skill을 맡은 서비스가 없으면 묻지 않는다. 그대로 부르면 꺼진 서비스로 보고
        # 사람 할 일함에 넘기기(hand-off) 때문이다. 인사가 services.py에 등록하면 그때부터 묻는다
        if route_target("hr", "team_headcount") == "hr":
            return None
        hit = self._headcount_cache.get(month)
        now = time.monotonic()
        if hit is not None and now - hit[0] < (
            HEADCOUNT_CACHE_SECONDS if hit[1] is not None else HEADCOUNT_FAILED_SECONDS
        ):
            return hit[1]
        deadline = min(meta.deadline, datetime.now(UTC) + timedelta(seconds=HR_CALL_SECONDS))
        request = AgentRequest(
            meta=meta.model_copy(update={"deadline": deadline}),
            skill="team_headcount",
            input=TeamHeadcountInput(month=month).model_dump(mode="json"),
        )
        result = await call_agent("hr", request)
        out: TeamHeadcountOutput | None = None
        if result.status == TaskStatus.COMPLETED:
            try:
                out = TeamHeadcountOutput.model_validate(result.output)
            except ValidationError:
                out = None
            if out is not None and out.month != month:
                out = None
        # 실패도 잠깐 기억한다. 화면이 몇 초마다 불러도 실패 기록이 AI 대화방에 쌓이지 않게
        self._headcount_cache[month] = (now, out)
        return out

    def forget_headcount(self, month: str) -> None:
        """기억해 둔 그 달 인원을 버린다. 다음 조회는 인사에 다시 묻는다."""
        self._headcount_cache.pop(month, None)

    async def _read_json(self, url: str, timeout: float) -> Any | None:
        """읽기 전용 GET. 응답이 없거나 200이 아니거나 JSON이 아니면 None (fail-closed)."""
        client = self._http or httpx.AsyncClient()
        try:
            response = await client.get(url, timeout=timeout)
        except httpx.HTTPError:
            return None
        finally:
            if self._http is None:
                await client.aclose()
        if response.status_code != 200:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    async def incident_window(self, context_id: str) -> dict[str, Any] | None:
        """총괄 사건 목록에서 이 사건의 구간·부품을 읽는다 (읽기 전용, 상태를 바꾸지 않음).

        사건 중에 부르지 않는다 — 평상시 사건 조회 경로에서만 읽는다
        ([협업] #417 제안 A, 요청 #418). 계약은 `data_access/orchestrator_incidents.py`.
        못 받거나 형식이 다르거나 그 사건이 없으면 None(확인할 수 없음)이고 추정하지 않는다.
        아직 열린 사건은 `window_to`가 None이라 구간이 되지 않는다(그때는 data에 묻지 않는다).
        """
        body = await self._read_json(
            f"{agent_url('orchestrator')}/api/incidents", ORCHESTRATOR_CALL_SECONDS
        )
        rows = body.get("data") if isinstance(body, dict) else None
        if not isinstance(rows, list):
            return None
        for row in rows:
            if not isinstance(row, dict) or row.get("context_id") != context_id:
                continue
            try:  # 합의한 다섯 키만 골라 검증한다 (총괄 응답에는 다른 키도 들어 있다)
                found = OrchestratorIncident.model_validate(
                    {k: row[k] for k in INCIDENT_FIELDS if k in row}
                )
            except ValidationError:
                return None
            # 해결로 닫힌 사건은 영향 구간이 더 바뀌지 않는다 → 영향 분석 결과를 계속 기억해도 된다.
            # degraded는 나중에 복구 신호로 구간이 바뀔 수 있어 아니다
            final = found.status == "resolved" and found.closed_at is not None
            if found.impact_from and found.impact_to:  # 총괄이 영향 분석에 쓴 구간 (정확)
                return {
                    "component": found.component,
                    "window_from": found.impact_from,
                    "window_to": found.impact_to,
                    "window_exact": True,
                    "recovered_at": found.recovered_at,
                    "category": None,  # 총괄은 분류를 주지 않는다
                    "final": final,
                }
            # 영향 구간을 안 실은 사건: AI 처리 시각으로 대신한다. 장애 시작보다 늦게 시작할 수 있어
            # 앞부분 영향 고객이 빠진다 → 근사로 표시하고 보상 대상 판정에 쓰지 않는다
            return {
                "component": found.component,
                "window_from": found.opened_at,
                "window_to": found.closed_at,
                "window_exact": False,
                "recovered_at": found.recovered_at,
                "category": None,
                "final": final,
            }
        return None
