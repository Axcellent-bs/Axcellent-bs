"""바깥 호출: 판매 사이트 장애 상태·해제(shop-api), 업무 지표 이상 탐지(data).

목 모드에서 주소(`AX_SHOP_API_URL`·`AX_DATA_URL`)가 없으면 가짜 응답을 쓴다(CI 단독 테스트용).
주소가 있으면 목 모드여도 실제로 부른다(템플릿 `execute()`와 같은 관례).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx

from ax_agent_template import call_agent
from ax_agent_template.company_connection import shop_api_override
from ax_agent_template.config import agent_token, agent_url, is_mock
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus

FAULTS_PATH = "/api/shop/admin/faults"
SHOP_TIMEOUT_SECONDS = 10.0
DATA_TIMEOUT_SECONDS = 20.0
PAYMENT_METRICS = ["payment_fail_rate", "order_fail_count"]


class ShopClient(Protocol):
    async def fault_state(self) -> dict[str, Any]: ...

    async def release_fault(self) -> dict[str, Any]: ...


class DataClient(Protocol):
    async def payment_metrics_ok(
        self, meta: RequestMeta, since: datetime, now: datetime
    ) -> tuple[bool | None, str]: ...


@dataclass
class MockShop:
    """목 판매 사이트. 기본은 장애 없음. 테스트에서 상태를 바꿔 쓴다."""

    state: dict[str, Any] = field(
        default_factory=lambda: {"active": False, "failing": False, "degraded": False}
    )
    released: int = 0

    async def fault_state(self) -> dict[str, Any]:
        return dict(self.state)

    async def release_fault(self) -> dict[str, Any]:
        self.released += 1
        self.state.update(active=False, failing=False, degraded=False)
        return dict(self.state)


@dataclass
class MockData:
    """목 데이터분석. 기본은 업무 지표 정상."""

    ok: bool | None = True
    calls: int = 0

    async def payment_metrics_ok(
        self, meta: RequestMeta, since: datetime, now: datetime
    ) -> tuple[bool | None, str]:
        self.calls += 1
        return self.ok, "목 데이터분석"


class HttpShop:
    """shop-api 관리 API. 해제는 infra 토큰만 허용된다(shop-api RELEASE_ALLOWED)."""

    def __init__(self, base_url: str | Callable[[], str]) -> None:
        # 함수를 주면 부를 때마다 주소를 다시 정한다 (회사 연결이 바뀌면 재시작 없이 따라감)
        self._base_url = base_url

    @property
    def base_url(self) -> str:
        raw = self._base_url() if callable(self._base_url) else self._base_url
        return raw.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {agent_token('infra')}"}

    async def fault_state(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=SHOP_TIMEOUT_SECONDS) as client:
            response = await client.get(self.base_url + FAULTS_PATH, headers=self._headers())
        response.raise_for_status()
        return dict(response.json().get("data") or {})

    async def release_fault(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=SHOP_TIMEOUT_SECONDS) as client:
            response = await client.delete(self.base_url + FAULTS_PATH, headers=self._headers())
        response.raise_for_status()
        return dict(response.json().get("data") or {})


ORCHESTRATOR_TIMEOUT_SECONDS = 5.0


async def orchestrator_opened_at(context_id: str) -> str | None:
    """총괄 사건 기록(`GET /api/incidents`)에서 이 사건이 열린 시각. 못 찾으면 None.

    롤백 교차검증(dev `verify_rollback_target`)이 사건 시각을 요구하는데 총괄이 롤백 요청에
    싣지 않을 때 쓴다. 지어내지 않는다: 기록이 없거나 못 읽으면 None이다.
    """
    if is_mock("infra"):
        return None
    try:
        async with httpx.AsyncClient(timeout=ORCHESTRATOR_TIMEOUT_SECONDS) as client:
            response = await client.get(f"{agent_url('orchestrator')}/api/incidents")
        response.raise_for_status()
        rows = response.json().get("data") or []
    except (httpx.HTTPError, ValueError):
        return None
    for row in rows:
        if isinstance(row, dict) and row.get("context_id") == context_id:
            opened = row.get("opened_at")
            return str(opened) if opened else None
    return None


class A2AData:
    """data `anomaly_detection`을 A2A로 부른다 (같은 사건 context_id)."""

    async def payment_metrics_ok(
        self, meta: RequestMeta, since: datetime, now: datetime
    ) -> tuple[bool | None, str]:
        deadline = min(meta.deadline, datetime.now(UTC) + timedelta(seconds=DATA_TIMEOUT_SECONDS))
        request = AgentRequest(
            meta=meta.model_copy(update={"deadline": deadline, "idempotency_key": None}),
            skill="anomaly_detection",
            input={
                "window_from": since.isoformat(),
                "window_to": now.isoformat(),
                "metrics": PAYMENT_METRICS,
            },
        )
        result = await call_agent("data", request)
        if result.status != TaskStatus.COMPLETED:
            return None, f"data anomaly_detection {result.status.value} ({result.outcome})"
        return business_verdict(result.output)


def business_verdict(output: dict[str, Any]) -> tuple[bool | None, str]:
    """data `anomaly_detection` 출력을 (정상 여부, 메모)로 바꾼다.

    판정 상태(`status`, #64 이후)가 있으면 그것만 본다. 정상(normal)이고 부른 지표 중
    판정할 수 없는 것이 없을 때만 True, 이상(anomaly)이면 False, 판정할 수 없으면(unknown,
    또는 지표 일부가 unknown) None이다. None은 복구를 확정하지 않는다는 뜻이다.
    `status`가 없는 예전 응답은 `any_anomaly`만 본다.
    """
    summary = str(output.get("summary") or "")
    status = output.get("status")
    if status is None:
        any_anomaly = output.get("any_anomaly")
        if not isinstance(any_anomaly, bool):
            return None, "data anomaly_detection 응답에 any_anomaly가 없음"
        return (not any_anomaly), summary
    if status == "anomaly":
        return False, summary
    unknown = [str(m) for m in output.get("unknown_metrics") or []]
    if status == "normal" and not unknown:
        return True, summary
    if status not in ("normal", "unknown"):
        return None, f"업무 지표를 판정할 수 없음: 알 수 없는 판정 상태 {status!r}"
    return None, "업무 지표를 판정할 수 없음: " + _unknown_reason(output, unknown, summary)


def _unknown_reason(output: dict[str, Any], unknown: list[str], summary: str) -> str:
    notes = [
        f"{v.get('metric')} {str(v['note']).removeprefix('확인할 수 없음: ')}"
        if v.get("note")
        else str(v.get("metric"))
        for v in output.get("verdicts") or []
        if isinstance(v, dict) and v.get("status") == "unknown"
    ]
    if notes:
        return "; ".join(notes)
    if unknown:
        return ", ".join(unknown) + (f" ({summary})" if summary else "")
    return summary or "사유 없음"


def shop_api_url() -> str:
    """shop-api 주소: 회사 연결 → `AX_SHOP_API_URL` → `http://shop-api:8010`."""
    return shop_api_override() or os.getenv("AX_SHOP_API_URL") or "http://shop-api:8010"


def default_shop() -> ShopClient:
    if not shop_api_override() and not os.getenv("AX_SHOP_API_URL") and is_mock("infra"):
        return MockShop()
    return HttpShop(shop_api_url)


def default_data() -> DataClient:
    if is_mock("infra") and not os.getenv("AX_DATA_URL"):
        return MockData()
    return A2AData()
