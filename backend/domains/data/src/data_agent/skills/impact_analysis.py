"""impact_analysis skill (DEP-DATA-02, 이슈 #54).

shop-api에서 실패 주문을 읽어 건수·금액·영향 고객·카테고리별 분포를 코드로 계산한다.
LLM은 `summary` 문장만 쓰며, 숫자가 어긋나면 고정 템플릿으로 대체한다(`fallback`).

장애 피해는 장애로 실패한 결제만 센다(정확도 평가 PR #55). shop-api 실패 주문의 `reason`
(failure_code)이 사용자 쪽 사유(카드 거절·한도 초과 등)면 빼고, 뺀 건수는 근거에 따로 적는다.
실패 사유가 없는 결제가 있으면 장애인지 알 수 없어 숫자를 내지 않는다(`ShopUnavailable`
`NO_FAILURE_REASON` → 핸들러가 "확인할 수 없음"으로 답한다).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from ax_agent_template.llm import LLMClient
from ax_contracts.data_access.shop_failed_orders import ShopFailedOrdersQuery
from ax_contracts.schemas import Evidence
from ax_contracts.schemas.data import CategoryImpact, ImpactAnalysisInput, ImpactAnalysisOutput
from data_agent.clients.shop_client import ShopClient, ShopUnavailable, get_shop_client
from data_agent.explain import explain
from data_agent.impact import aggregate_impact, split_failures


@dataclass(frozen=True)
class ImpactResult:
    output: ImpactAnalysisOutput
    evidence: Evidence
    fallback: bool


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


async def analyze_impact(
    data: ImpactAnalysisInput,
    tenant_id: str,
    llm: LLMClient,
    env: Mapping[str, str] | None = None,
    shop: ShopClient | None = None,
) -> ImpactResult:
    """장애 구간의 영향을 계산한다. 외부 클라이언트가 없으면 `get_shop_client(env)`를 쓴다."""
    client = shop or (get_shop_client(env) if env is not None else get_shop_client())
    query = ShopFailedOrdersQuery(
        from_=data.window_from, to=data.window_to, category=data.category, tenant_id=tenant_id
    )
    result = await client.list_failed_orders(query)

    split = split_failures(result)
    if split.missing_reason:
        raise ShopUnavailable(
            "NO_FAILURE_REASON",
            f"실패 사유가 없는 결제 {split.missing_reason}건이 있어 장애 피해인지 알 수 없다",
        )
    counts = aggregate_impact(split.fault)  # 음수 금액이면 ValueError
    customer_ids = sorted({order.buyer_id for order in split.fault})
    by_category = _category_distribution(split.fault)

    explanation = await explain(
        "impact",
        {
            "failed_order_count": counts.failed_order_count,
            "affected_customer_count": counts.affected_customer_count,
            "damage_amount": counts.damage_amount,
        },
        llm,
    )
    output = ImpactAnalysisOutput(
        failed_order_count=counts.failed_order_count,
        affected_customer_count=len(customer_ids),
        affected_customer_ids=customer_ids,
        damage_amount=counts.damage_amount,
        by_category=by_category,
        summary=explanation.summary,
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
    for name, count in sorted(split.excluded.items()):
        summary += f"\n{name} {count}건은 장애 피해에서 제외"
    if result.truncated:
        summary += f"\n조회 한도 {query.limit}건에 걸려 일부만 반영됨(truncated)"
    evidence = Evidence(
        queries=[condition],
        data_summary=summary,
        collected_at=datetime.now(UTC),
    )
    return ImpactResult(output=output, evidence=evidence, fallback=explanation.fallback)
