"""impact_analysis skill (DEP-DATA-02, 이슈 #54).

shop-api에서 실패 주문을 읽어 건수·금액·영향 고객·카테고리별 분포를 코드로 계산한다.
LLM은 `summary` 문장만 쓰며, 숫자가 어긋나면 고정 템플릿으로 대체한다(`fallback`).

장애 피해는 장애로 실패한 결제만 센다(정확도 평가 PR #55). shop-api 실패 주문의 `reason`
(failure_code)이 장애 쪽 코드(`impact.FAULT_REASONS`)인 것만 세고, 사용자 쪽 사유(카드 거절·
한도 초과 등)는 빼서 근거에 따로 적는다. 아래면 장애인지 알 수 없어 숫자를 내지 않는다
(`ShopUnavailable` → 핸들러가 "확인할 수 없음"으로 답한다).
- 실패 사유가 없는 결제 (`NO_FAILURE_REASON`)
- 둘 다 아닌 처음 보는 실패 코드 (`UNKNOWN_FAILURE_CODE`, "처음 보는 실패 코드 N건")

초과분(`excess_failed_order_count`): 장애 때문에 평소보다 더 실패한 건수다.
- 평소 = 지난 7일 같은 시각 ±1시간의 장애 쪽 실패를 결제가 기록된 5분 평균으로 구해 구간 길이만큼
  곱한 값. 초과분 = 구간 장애 쪽 실패 − 평소 (반올림, 0 미만이면 0)
- 지난 7일 자료가 없거나 읽지 못하면 None(확인할 수 없음). 결제 수를 주지 않는 클라이언트(목)도 None
- 계산된 초과분과 평소 실패 수(소수 둘째 자리)는 LLM 설명의 facts에도 넣는다. 그래서 설명이 이 두
  숫자를 써도 통과하고, 계산에 없는 숫자는 계속 막는다(`explain._numbers_valid`). 모르면 넣지 않는다
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ax_agent_template.llm import LLMClient
from ax_contracts.data_access.shop_failed_orders import (
    ShopFailedOrdersQuery,
)
from ax_contracts.schemas import Evidence
from ax_contracts.schemas.data import CategoryImpact, ImpactAnalysisInput, ImpactAnalysisOutput
from data_agent.clients.shop_client import ShopUnavailable, get_shop_client
from data_agent.connectors import Connector, ConnectorUnavailable, as_connector
from data_agent.explain import Explanation, explain
from data_agent.impact import (
    EXCESS_DAYS,
    EXCESS_MARGIN,
    ImpactCounts,
    aggregate_impact,
    excess_failures,
    split_failures,
    usual_fault_failures,
)

HISTORY_LIMIT = 5000  # 지난 7일 하루치 실패 주문 조회 한도 (shop-api 최대)


async def _usual(
    connector: Connector, data: ImpactAnalysisInput, tenant_id: str
) -> tuple[float | None, str]:
    """평소 장애 쪽 실패(구간 길이만큼)와 근거 한 줄. 모르면 (None, 이유).

    커넥터의 거래 시도 수(`transactions_volume`)와 실패 이벤트로 센다. 거래 시도 수를 주지 않는
    커넥터(목 등)면 확인할 수 없음이다.
    """
    payments: dict = {}
    fault_times: list[datetime] = []
    try:
        for day in range(1, EXCESS_DAYS + 1):
            start = data.window_from - timedelta(days=day) - EXCESS_MARGIN
            end = data.window_to - timedelta(days=day) + EXCESS_MARGIN
            volume = await connector.transactions_volume(start, end)
            if volume is None:
                return None, (
                    "평소 비교: 확인할 수 없음 (지난 7일 결제 수를 읽을 수 없는 클라이언트)"
                )
            payments.update(dict(volume))
            past = await connector.failure_events(
                start, end, category=data.category, limit=HISTORY_LIMIT, tenant_id=tenant_id
            )
            fault_times += [o.at for o in split_failures(past).fault]
    except ConnectorUnavailable as error:
        return None, f"평소 비교: 확인할 수 없음 (지난 7일 조회 실패 {error.code})"
    usual = usual_fault_failures(payments, fault_times, data.window_to - data.window_from)
    if usual is None:
        return None, "평소 비교: 확인할 수 없음 (지난 7일 같은 시각 ±1시간에 결제 기록이 없음)"
    return usual, (
        f"평소 비교: 지난 7일 같은 시각 ±1시간 장애 쪽 실패 {len(fault_times)}건, "
        f"구간 길이로 환산한 평소 {usual:.2f}건"
    )


@dataclass(frozen=True)
class ImpactResult:
    output: ImpactAnalysisOutput
    evidence: Evidence
    fallback: bool
    fallback_reason: str | None = None  # 설명을 템플릿으로 대체한 이유 (`explain.FALLBACK_REASONS`)
    fallback_numbers: tuple[float, ...] = ()  # `number_invalid`일 때 계산에 없던 숫자


def _category_distribution(orders: list) -> list[CategoryImpact]:
    """카테고리별 실패 건수·피해 금액. 카테고리 이름순으로 정렬한다."""
    counts: dict[str, int] = {}
    amounts: dict[str, int] = {}
    for order in orders:
        counts[order.category] = counts.get(order.category, 0) + 1
        amounts[order.category] = amounts.get(order.category, 0) + order.amount
    return [
        CategoryImpact(
            category=category, failed_order_count=counts[category], damage_amount=amounts[category]
        )
        for category in sorted(counts)
    ]


def impact_facts(
    counts: ImpactCounts, excess: int | None, usual: float | None
) -> dict[str, int | float | str]:
    """영향 설명에 넘기는 숫자 (설명이 쓸 수 있는 숫자는 이것뿐이다).

    초과분·평소 실패 수는 계산했을 때만 넣는다. 평소 실패 수는 근거 문장과 같은 소수 둘째 자리다.
    """
    facts: dict[str, int | float | str] = {
        "failed_order_count": counts.failed_order_count,
        "affected_customer_count": counts.affected_customer_count,
        "damage_amount": counts.damage_amount,
    }
    if excess is not None:
        facts["excess_failed_order_count"] = excess
    if usual is not None:
        facts["usual_fault_failures"] = round(usual, 2)
    return facts


async def analyze_impact(
    data: ImpactAnalysisInput,
    tenant_id: str,
    llm: LLMClient,
    env: Mapping[str, str] | None = None,
    shop: object | None = None,
    connector: Connector | None = None,
) -> ImpactResult:
    """장애 구간의 영향을 계산한다. 커넥터만 쓴다(`connector`, 예전 이름 `shop`).

    둘 다 없으면 회사 카탈로그의 커넥터(`get_shop_client(env)`)다. 예전 공급자를 주면 감싼다.
    """
    source = connector or shop or (get_shop_client(env) if env is not None else get_shop_client())
    conn = as_connector(source)
    limit = ShopFailedOrdersQuery.model_fields["limit"].default
    result = await conn.failure_events(
        data.window_from,
        data.window_to,
        category=data.category,
        limit=limit,
        tenant_id=tenant_id,
    )

    split = split_failures(result)
    if split.missing_reason:
        raise ShopUnavailable(
            "NO_FAILURE_REASON",
            f"실패 사유가 없는 결제 {split.missing_reason}건이 있어 장애 피해인지 알 수 없다",
        )
    if split.unseen:
        codes = ", ".join(f"{code} {count}건" for code, count in sorted(split.unseen.items()))
        raise ShopUnavailable(
            "UNKNOWN_FAILURE_CODE",
            f"처음 보는 실패 코드 {sum(split.unseen.values())}건 ({codes})이 있어 "
            "장애 피해인지 알 수 없다",
        )
    counts = aggregate_impact(split.fault)  # 음수 금액이면 ValueError
    customer_ids = sorted({event.customer_id for event in split.fault})
    by_category = _category_distribution(split.fault)

    usual, usual_note = await _usual(conn, data, tenant_id)
    excess = excess_failures(counts.failed_order_count, usual)
    explanation = await explain("impact", impact_facts(counts, excess, usual), llm)
    text = explanation.summary
    if excess is not None:
        text = f"{text} 평소보다 {excess}건 많음."
    output = ImpactAnalysisOutput(
        failed_order_count=counts.failed_order_count,
        affected_customer_count=len(customer_ids),
        affected_customer_ids=customer_ids,
        damage_amount=counts.damage_amount,
        by_category=by_category,
        summary=text,
        excess_failed_order_count=excess,
    )
    condition = (
        f"shop_failed_orders from={data.window_from.isoformat()} "
        f"to={data.window_to.isoformat()} category={data.category or 'all'} "
        f"scope={data.scope or 'all'}"
    )
    summary = (
        f"실패 주문 {counts.failed_order_count}건, 영향 고객 {len(customer_ids)}명, "
        f"피해 금액 {counts.damage_amount}원, 카테고리 {len(by_category)}개"
    )
    summary += f"\n{usual_note}" + (f", 초과분 {excess}건" if excess is not None else "")
    for name, count in sorted(split.excluded.items()):
        summary += f"\n{name} {count}건은 장애 피해에서 제외"
    if result.truncated:
        summary += f"\n조회 한도 {limit}건에 걸려 일부만 반영됨(truncated)"
    if note := explanation.note():
        summary += f"\n{note}"
    evidence = Evidence(
        queries=[condition],
        data_summary=summary,
        collected_at=datetime.now(UTC),
    )
    return _result(output, evidence, explanation)


def _result(
    output: ImpactAnalysisOutput, evidence: Evidence, explained: Explanation
) -> ImpactResult:
    return ImpactResult(
        output=output,
        evidence=evidence,
        fallback=explained.fallback,
        fallback_reason=explained.reason,
        fallback_numbers=explained.bad_numbers,
    )
