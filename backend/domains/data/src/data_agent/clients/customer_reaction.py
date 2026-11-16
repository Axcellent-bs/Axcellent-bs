"""고객지원 `book_issue_stats` 조회 (브리프 연결 상품의 고객 반응, A2A).

다른 domains 코드는 import하지 않고 `call_agent`로만 부른다.
숫자는 고객지원이 센 값 그대로 옮기고 데이터는 세지 않는다.
한 브리프에서 같은 기간은 한 번만 부르고, 읽은 결과는 하루(한국 날짜) 캐시한다.
실패(꺼짐·무응답·시간 초과·형식 오류)는 캐시하지 않고 이유와 함께 돌려준다.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ax_agent_template.client import call_agent
from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from ax_contracts.schemas.data import CustomerReaction, TrendInsight

SendFn = Callable[[str, AgentRequest], Awaitable[AgentResult]]

TARGET = "support"  # 부서 이름으로 부르면 skill을 맡은 서비스(claims)로 간다 (services.py)
SKILL = "book_issue_stats"
SOURCE = f"support.{SKILL}"
PERIOD = "month"
PERIOD_LABEL = "최근 한 달"
MIN_SOLD = 5  # 판매 수량이 이보다 적으면 비율이 흔들려 넣지 않는다
MAX_PRODUCTS = 100  # book_issue_stats 입력 상한
TIMEOUT_SECONDS = 8.0
OVER_NOTE = "접수 기간 기준"  # claim_rate가 1을 넘을 때 (오래전 주문의 클레임도 들어감)


class _Row(BaseModel):
    """book_issue_stats 상품 한 줄 중 쓰는 칸만 (계약은 고객지원 쪽, 나머지 칸은 무시)."""

    model_config = ConfigDict(extra="ignore")
    product_id: str = Field(min_length=1)
    claim_count: int = Field(ge=0)
    defect_claim_count: int = Field(ge=0)
    sold_qty: int | None = Field(default=None, ge=0)
    claim_rate: float | None = Field(default=None, ge=0)


class _Report(BaseModel):
    model_config = ConfigDict(extra="ignore")
    items: list[_Row] = Field(default_factory=list)
    sales_read: bool = False


@dataclass(frozen=True)
class Reactions:
    """조회 결과. `reason`이 있으면 읽지 못한 것이다 (rows는 비어 있음)."""

    rows: dict[str, _Row] = field(default_factory=dict)
    asked: frozenset[str] = frozenset()
    sales_read: bool = False
    reason: str | None = None


def _failed(reason: str) -> Reactions:
    return Reactions(reason=reason)


_CACHE: dict[tuple[str, date, str], Reactions] = {}
_LOCK = threading.Lock()


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def _today_kst(now: datetime) -> date:
    return (now.astimezone(UTC) + timedelta(hours=9)).date()


async def read(
    product_ids: Sequence[str],
    meta: RequestMeta,
    send: SendFn | None = None,
    now: datetime | None = None,
) -> Reactions:
    """상품들의 최근 한 달 클레임·판매 수량. 같은 날 이미 읽은 상품이면 다시 부르지 않는다."""
    ids = list(dict.fromkeys(p for p in product_ids if p))[:MAX_PRODUCTS]
    if not ids:
        return Reactions()
    now = now or datetime.now(UTC)
    key = (meta.tenant_id, _today_kst(now), PERIOD)
    with _LOCK:
        hit = _CACHE.get(key)
    if hit is not None and set(ids) <= hit.asked:
        return hit
    if hit is not None:  # 새 상품이 섞이면 전에 읽은 상품과 합쳐 한 번에 다시 읽는다
        ids = list(dict.fromkeys([*ids, *sorted(hit.asked)]))[:MAX_PRODUCTS]
    got = await _call(ids, meta, send or call_agent)
    if got.reason is None:
        with _LOCK:
            _CACHE[key] = got
    return got


async def _call(ids: list[str], meta: RequestMeta, send: SendFn) -> Reactions:
    deadline = min(meta.deadline, datetime.now(UTC) + timedelta(seconds=TIMEOUT_SECONDS))
    request = AgentRequest(
        meta=meta.model_copy(update={"deadline": deadline}),
        skill=SKILL,
        input={
            "period": PERIOD,
            "product_ids": ids,
            "limit": len(ids),
            "include_inquiries": False,
            "include_sales": True,
        },
    )
    try:
        result = await asyncio.wait_for(send(TARGET, request), timeout=TIMEOUT_SECONDS)
    except TimeoutError:
        return _failed(f"{SOURCE} 시간 초과 ({TIMEOUT_SECONDS:g}초)")
    except Exception as error:  # noqa: BLE001 - 보조 조회라 어떤 실패든 읽지 못함으로 둔다
        return _failed(f"{SOURCE} 호출 실패 ({type(error).__name__})")
    if result.status == TaskStatus.REJECTED and result.outcome == "service_disabled":
        return _failed(f"{SOURCE} 서비스가 꺼져 있음")
    if result.status != TaskStatus.COMPLETED:
        return _failed(f"{SOURCE} 응답 {result.status.value} ({result.outcome or '-'})")
    try:
        report = _Report.model_validate(result.output or {})
    except ValidationError:
        return _failed(f"{SOURCE} 응답 형식이 계약과 다름")
    return Reactions(
        rows={r.product_id: r for r in report.items},
        asked=frozenset(ids),
        sales_read=report.sales_read,
    )


def for_product(
    got: Reactions, product_id: str, name: str
) -> tuple[CustomerReaction | None, str | None]:
    """상품 하나의 고객 반응 칸, 또는 넣지 않은 이유 (둘 중 하나)."""
    label = f"고객 반응 · {name}"
    if got.reason is not None:
        return None, f"{label}: 읽지 못함 ({got.reason})"
    row = got.rows.get(product_id)
    if row is None:
        return None, f"{label}: {PERIOD_LABEL} 클레임이 없어 집계에 없음 (판매 수량 모름)"
    if row.sold_qty is None:
        return None, f"{label}: 판매 수량을 읽지 못함"
    if row.sold_qty < MIN_SOLD:
        few = f"판매 {row.sold_qty}권 ({MIN_SOLD}권 미만이라 넣지 않음)"
        return None, f"{label}: {PERIOD_LABEL} {few}"
    rate = row.claim_rate
    return (
        CustomerReaction(
            product_id=product_id,
            product_name=name,
            period=PERIOD,
            claim_count=row.claim_count,
            defect_count=row.defect_claim_count,
            sold_qty=row.sold_qty,
            claim_rate=rate,
            note=OVER_NOTE if rate is not None and rate > 1 else None,
        ),
        None,
    )


def fill(insights: Sequence[TrendInsight], got: Reactions) -> int:
    """인사이트마다 연결 상품의 고객 반응을 채운다. 넣지 않은 상품은 evidence.missing에 이유.

    채운 칸 수를 돌려준다."""
    filled = 0
    for i in insights:
        ev = i.evidence
        for p in i.products:
            got_one, why = for_product(got, p.product_id, p.name)
            if got_one is not None:
                ev.customer_reaction.append(got_one)
                filled += 1
            elif why and why not in ev.missing:
                ev.missing.append(why)
    return filled
