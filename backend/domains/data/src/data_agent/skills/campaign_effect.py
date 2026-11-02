"""campaign_effect: 캠페인 효과 (주문·매출 증분, 캠페인 쿠폰 누적 사용률). 읽기 전용(grade read).

계산은 모두 코드다. LLM은 전체 요약 문장만 쓴다(`explain` 숫자 검증 그대로).

- 주문: 캠페인 기간 `order_count` 합계(실제)를 지난 7일 같은 시간대 합계의 평균(기대)과 비교한다.
  증분 = 실제 − 기대 (평가 채점기 `eval/campaign/score.py`와 같은 정의). 방향은 기대를 평균으로 한
  포아송 꼬리확률(`metrics.judge.poisson_upper`·`poisson_lower`)이 `DIRECTION_ALPHA`보다 작을 때만
  늘었음·줄었음이고, 아니면 같음이다. 지표는 커넥터 `metric_series`로 읽는다.
- 매출: 카탈로그 `revenue_metric`이 있으면 같은 방법으로 합계·기대·증분을 낸다
  (방향은 ±10% 임시 기준).
  캣북스는 비어 있다(shop-api metric-series에 매출이 없음) → 확인할 수 없음 + 이유.
- 쿠폰: 커넥터 `campaign_coupons`(shop-api `GET /api/shop/coupons?campaign_id=`)의 발급·사용 장수로
  누적 사용률 = 사용 ÷ 발급. 설명은 고정 문장(`COUPON_NOTE`)이다(LLM이 아님).
- 기록 공백은 "줄었음"이 아니라 unknown + 이유다(shop-api는 주문이 없는 5분을 0으로 준다):
  - 구간에 주문·결제 기록이 하나도 없음: 이상 탐지와 같은 함수(`judge.empty_window`)·문장
    (`detect.empty_reason`)
  - 결제 시도 0인 5분 칸 비율이 지난 7일 같은 시간대보다 `GAP_SHARE` 이상 높고 `GAP_MIN_BUCKETS`칸
    이상 많음: "기록 공백 의심 (N칸)". 통째로 빈 지난날은 비교에서 뺀다(그날도 공백이라서)
- 판정할 수 없으면 0으로 채우지 않고 unknown + 이유: 지난 7일 같은 시간대 주문이 0건(기준선 없음),
  캠페인이 하루보다 김(지난날 같은 시간대가 캠페인과 겹침), campaign_id 없음, 캠페인 쿠폰 없음.
- 주문 지표를 못 읽으면(shop-api 꺼짐) `ConnectorUnavailable`을 그대로 올린다
  (핸들러가 숫자 없이 실패로 답함).

기준값(`DIRECTION_ALPHA`)은 평가 개발 케이스(`eval/campaign/cases/`)로 정했다.
README "campaign_effect".
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ax_agent_template.llm import LLMClient
from ax_contracts.schemas import Evidence
from ax_contracts.schemas.data import (
    CampaignChange,
    CampaignCoupons,
    CampaignEffectInput,
    CampaignEffectOutput,
)
from data_agent import catalog
from data_agent.connectors import ConnectorUnavailable
from data_agent.detect import empty_reason
from data_agent.explain import explain
from data_agent.metrics.judge import (
    BASELINE_DAYS,
    empty_window,
    poisson_lower,
    poisson_upper,
    to_buckets,
    window_buckets,
)

ORDER_METRIC = "order_count"
DIRECTION_ALPHA = 0.001  # 방향 판정 유의수준 (평가 개발 케이스로 정함, README)
REVENUE_RATIO_BAND = 0.10  # 매출 방향 임시 기준: 기대 대비 ±10% (매출 지표가 생기면 평가로 정한다)
MAX_WINDOW = timedelta(days=1)  # 이보다 길면 지난날 같은 시간대가 캠페인과 겹친다
COUPON_NOTE = "조회 시점 기준 누적 사용률이며, 캠페인 뒤 사용분도 섞여 있다"
DIRECTION_KO = {"up": "늘었음", "none": "같음", "down": "줄었음"}
# 기록 공백 의심: 결제 시도 0인 5분 칸 비율이 지난 7일보다 이만큼 높고, 칸 수로도 이만큼 많을 때
# 근거(README): 평소 새벽 1시간은 비율이 28.6%p까지 튀지만 칸은 3.4칸 이하,
# 평소 24시간은 1.6%p·4.7칸 이하. 3시간 공백은 낮 10.5%p·30칸 이상, 새벽 7.3%p·21칸 이상.
# 1시간보다 짧은 공백은 잡지 않는다
GAP_SHARE = 0.05  # 5%p
GAP_MIN_BUCKETS = 12  # 1시간


# ---------------------------------------------------------------- 계산 (코드)


async def _total(connector: Any, metric: str, start: datetime, end: datetime) -> float | None:
    """[start, end) 지표 합계. 점이 하나도 없으면 None(자료 없음, 0이 아님)."""
    points = await connector.metric_series(metric, start, end)
    values = [v for t, v in points if start <= t < end and v is not None]
    return float(sum(values)) if values else None


async def _measure(
    connector: Any, metric: str, start: datetime, end: datetime
) -> tuple[float | None, list[float]]:
    """(캠페인 기간 합계, 지난 7일 각 날 같은 시간대 합계 목록 — 자료가 있는 날만)."""
    actual = await _total(connector, metric, start, end)
    past = []
    for day in range(1, BASELINE_DAYS + 1):
        shift = timedelta(days=day)
        value = await _total(connector, metric, start - shift, end - shift)
        if value is not None:
            past.append(value)
    return actual, past


async def _payment_zero_share(
    connector: Any, start: datetime, end: datetime
) -> tuple[int, int] | None:
    """(결제 시도 0인 5분 칸 수, 전체 칸 수). 결제 시도를 주지 않는 커넥터면 None."""
    volume = getattr(connector, "transactions_volume", None)
    points = await volume(start, end) if volume else None
    if points is None:
        return None
    payments = to_buckets(points)
    slots = window_buckets(start, end)
    return sum(1 for b in slots if not payments.get(b)), len(slots)


async def gap_reason(connector: Any, start: datetime, end: datetime) -> str | None:
    """구간 기록이 비었거나 공백이 의심되면 그 이유, 아니면 None."""
    slots = window_buckets(start, end)
    orders = to_buckets(await connector.metric_series(ORDER_METRIC, start, end))
    volume = getattr(connector, "transactions_volume", None)
    points = await volume(start, end) if volume else None
    if empty_window(slots, orders, to_buckets(points or [])):
        return empty_reason()  # 이상 탐지 빈 구간과 같은 문장
    if points is None:
        return None  # 결제 시도를 주지 않는 커넥터: 공백 비율은 보지 않는다
    zeros = sum(1 for b in slots if not to_buckets(points).get(b))
    past_zeros = past_slots = 0
    for day in range(1, BASELINE_DAYS + 1):
        shift = timedelta(days=day)
        past = await _payment_zero_share(connector, start - shift, end - shift)
        if past is None or past[0] == past[1]:
            continue  # 통째로 빈 날은 그날도 공백이라 평소 비율에 넣지 않는다
        past_zeros += past[0]
        past_slots += past[1]
    if not past_slots:
        return None  # 비교할 평소가 없으면 기준선 규칙(0건)이 판단한다
    usual = past_zeros / past_slots
    share = zeros / len(slots)
    if share - usual >= GAP_SHARE and zeros - usual * len(slots) >= GAP_MIN_BUCKETS:
        return (
            f"기록 공백 의심 ({zeros}칸): 결제 시도 0인 5분 칸이 {share:.0%}로 지난 7일 같은 시간대"
            f" {usual:.0%}보다 많음 (자료가 빠졌을 수 있어 줄었다고 하지 않음)"
        )
    return None


def _unknown_change(reason: str, baseline_days: int = 0) -> CampaignChange:
    return CampaignChange(status="unknown", unknown_reason=reason, baseline_days=baseline_days)


def _numbers(actual: float, past: list[float]) -> dict[str, Any]:
    expected = sum(past) / len(past)
    return {
        "actual": actual,
        "expected": round(expected, 4),
        "excess": round(actual - expected, 4),
        "ratio": round(actual / expected, 4),
        "baseline_days": len(past),
    }


def judge_orders(
    actual: float | None, past: list[float], alpha: float = DIRECTION_ALPHA
) -> CampaignChange:
    """주문 수 변화. 기대를 평균으로 한 포아송 꼬리확률로 방향을 정한다."""
    if actual is None or not past:
        return _unknown_change("캠페인 기간·지난 7일 같은 시간대 주문 자료가 없음", len(past))
    expected = sum(past) / len(past)
    if expected == 0:
        return _unknown_change(
            "지난 7일 같은 시간대 주문이 0건이라 기준선을 정할 수 없음", len(past)
        )
    observed = int(round(actual))
    p_up = poisson_upper(observed, expected) if observed > expected else 1.0
    p_down = poisson_lower(observed, expected) if observed < expected else 1.0
    direction = "up" if p_up < alpha else "down" if p_down < alpha else "none"
    return CampaignChange(
        status="known",
        direction=direction,
        p_value=min(p_up, p_down, 1.0),
        **_numbers(actual, past),
    )


def judge_revenue(actual: float | None, past: list[float]) -> CampaignChange:
    """매출 변화. 금액이라 포아송을 쓰지 않고 기대 대비 ±10%로 방향을 정한다(임시)."""
    if actual is None or not past:
        return _unknown_change("캠페인 기간·지난 7일 같은 시간대 매출 자료가 없음", len(past))
    expected = sum(past) / len(past)
    if expected == 0:
        return _unknown_change("지난 7일 같은 시간대 매출이 0이라 기준선을 정할 수 없음", len(past))
    ratio = actual / expected
    direction = (
        "up"
        if ratio >= 1 + REVENUE_RATIO_BAND
        else "down"
        if ratio <= 1 - REVENUE_RATIO_BAND
        else "none"
    )
    return CampaignChange(status="known", direction=direction, **_numbers(actual, past))


async def _coupons(connector: Any, campaign_id: str | None) -> CampaignCoupons:
    if not campaign_id:
        return CampaignCoupons(
            status="unknown", unknown_reason="campaign_id가 없어 캠페인 쿠폰을 찾을 수 없음"
        )
    if not hasattr(connector, "campaign_coupons"):
        return CampaignCoupons(status="unknown", unknown_reason="이 회사 커넥터는 쿠폰을 주지 않음")
    try:
        coupons = await connector.campaign_coupons(campaign_id)
    except ConnectorUnavailable as error:
        return CampaignCoupons(status="unknown", unknown_reason=f"쿠폰을 읽지 못함 [{error.code}]")
    issued = sum(c.count for c in coupons)
    if not coupons or issued == 0:
        return CampaignCoupons(
            status="unknown",
            coupon_count=len(coupons),
            unknown_reason=f"캠페인 {campaign_id} 쿠폰이 없음",
        )
    used = sum(c.used_units for c in coupons)
    return CampaignCoupons(
        status="known",
        coupon_count=len(coupons),
        issued_units=issued,
        used_units=used,
        use_rate=round(min(used / issued, 1.0), 4),
        note=COUPON_NOTE,
    )


async def measure(
    data: CampaignEffectInput, connector: Any, *, alpha: float = DIRECTION_ALPHA
) -> CampaignEffectOutput:
    """설명 문장 없이 숫자만 계산한다 (평가 채점기도 이 함수를 부른다)."""
    start, end = data.window_from, data.window_to
    if end - start > MAX_WINDOW:
        reason = "캠페인이 하루보다 길어 지난 7일 같은 시간대가 캠페인과 겹침 (1단계는 하루 이하만)"
        orders, revenue = _unknown_change(reason), _unknown_change(reason)
    elif gap := await gap_reason(connector, start, end):
        orders, revenue = _unknown_change(gap), _unknown_change(gap)
    else:
        orders = judge_orders(*await _measure(connector, ORDER_METRIC, start, end), alpha=alpha)
        revenue_metric = catalog.current().revenue_metric
        if revenue_metric:
            revenue = judge_revenue(*await _measure(connector, revenue_metric, start, end))
        else:
            revenue = _unknown_change(
                "매출 지표가 없음 (shop-api metric-series에 매출이 없고"
                " 카탈로그 revenue_metric이 비어 있음)"
            )
    return CampaignEffectOutput(
        campaign_id=data.campaign_id,
        window_from=start,
        window_to=end,
        orders=orders,
        revenue=revenue,
        coupons=await _coupons(connector, data.campaign_id),
        summary="",
    )


# ---------------------------------------------------------------- 설명 (LLM은 요약 문장만)


def _facts(out: CampaignEffectOutput) -> dict[str, int | float | str]:
    facts: dict[str, int | float | str] = {}
    o = out.orders
    if o.status == "known":
        direction = DIRECTION_KO[o.direction or "none"]
        if o.direction == "none" and o.excess:  # 화면(#294)과 같은 규칙
            direction = f"{'증가' if o.excess > 0 else '감소'} 추정, 아직 통계적으로 확실하지 않음"
        facts |= {
            "주문 방향": direction,
            "캠페인 주문": int(o.actual or 0),
            "평소 주문": round(o.expected or 0, 1),
            "주문 증분": round(o.excess or 0, 1),
            "기준 일수": o.baseline_days,
        }
    else:
        facts["주문"] = f"확인할 수 없음: {o.unknown_reason}"
    c = out.coupons
    if c.status == "known":
        facts |= {"쿠폰 사용률": c.use_rate or 0.0, "쿠폰 발급": c.issued_units or 0,
                  "쿠폰 사용": c.used_units or 0}  # fmt: skip
    else:
        facts["쿠폰"] = f"확인할 수 없음: {c.unknown_reason}"
    if out.revenue.status == "unknown":
        facts["매출"] = "확인할 수 없음"
    return facts


@dataclass(frozen=True)
class CampaignEffectResult:
    output: CampaignEffectOutput
    evidence: Evidence
    fallback: bool
    summary: str | None
    fallback_reason: str | None = None
    fallback_numbers: tuple[float, ...] = ()


async def analyze_campaign(
    data: CampaignEffectInput, connector: Any, llm: LLMClient
) -> CampaignEffectResult:
    measured = await measure(data, connector)
    said = await explain("campaign", _facts(measured), llm)
    output = measured.model_copy(update={"summary": said.summary})
    span = f"{data.window_from.isoformat()}~{data.window_to.isoformat()}"
    queries = [
        f"shop_metric_series metric={ORDER_METRIC} {span} + 지난 {BASELINE_DAYS}일 같은 시간대"
    ]
    if data.campaign_id:
        queries.append(f"shop_coupons campaign_id={data.campaign_id}")
    notes = [said.summary]
    for name, part in (("주문", output.orders), ("매출", output.revenue), ("쿠폰", output.coupons)):
        if part.status == "unknown":
            notes.append(f"{name} 확인할 수 없음: {part.unknown_reason}")
    if note := said.note():
        notes.append(note)
    return CampaignEffectResult(
        output=output,
        evidence=Evidence(
            queries=queries, data_summary="\n".join(notes), collected_at=datetime.now(UTC)
        ),
        fallback=said.fallback,
        summary=said.summary,
        fallback_reason=said.reason,
        fallback_numbers=said.bad_numbers,
    )


# ---------------------------------------------------------------- 평가 채점기용 (campaign/score.py)


def to_answer(out: CampaignEffectOutput) -> dict[str, Any]:
    """skill 출력 → 채점기 답 모양(`score.Answer`)."""

    def change(c: CampaignChange) -> dict[str, Any]:
        if c.status == "unknown":
            return {"status": "unknown", "unknown_reason": c.unknown_reason}
        return {"status": "known", "direction": c.direction, "excess": c.excess}

    coupons = out.coupons
    coupon = (
        {"status": "unknown", "unknown_reason": coupons.unknown_reason}
        if coupons.status == "unknown"
        else {"status": "known", "use_rate": coupons.use_rate, "note": coupons.note}
    )
    return {"orders": change(out.orders), "revenue": change(out.revenue), "coupon": coupon}


async def eval_judge(
    request: Mapping[str, Any], transport: httpx.MockTransport, *, alpha: float = DIRECTION_ALPHA
) -> dict[str, Any]:
    """채점기가 부르는 판정 함수: 케이스 데이터로 만든 shop-api를 실제 커넥터로 읽는다."""
    from data_agent.connectors.shop_api import ShopApi, ShopApiConnector

    async with httpx.AsyncClient(transport=transport) as client:
        connector = ShopApiConnector(ShopApi("http://shop.eval", token="eval", client=client))
        out = await measure(
            CampaignEffectInput.model_validate(dict(request)), connector, alpha=alpha
        )
    return to_answer(out)
