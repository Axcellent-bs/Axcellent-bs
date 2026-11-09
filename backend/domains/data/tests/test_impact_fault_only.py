"""영향 분석은 장애로 실패한 결제만 센다 (정확도 평가 PR #55에서 나온 문제).

- 사용자 쪽 사유(카드 거절·한도 초과 등)는 빼고, 뺀 건수를 근거에 적는다.
- 실패 사유가 없는 결제가 있으면 장애인지 알 수 없어 숫자를 내지 않는다("확인할 수 없음").
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template.llm import MockLLMClient
from ax_contracts.data_access.shop_failed_orders import (
    FailedOrder,
    ShopFailedOrdersQuery,
    ShopFailedOrdersResult,
)
from ax_contracts.schemas.data import ImpactAnalysisInput
from data_agent import handlers
from data_agent.clients.shop_client import ShopUnavailable
from data_agent.impact import split_failures
from data_agent.main import build_app
from data_agent.skills import impact_analysis as impact_skill
from data_agent.skills.impact_analysis import analyze_impact

AT = datetime(2026, 9, 1, 10, 30, tzinfo=UTC)
WINDOW = ImpactAnalysisInput(window_from=AT - timedelta(minutes=30), window_to=AT)


def _order(oid: str, buyer: str, amount: int, reason: str) -> FailedOrder:
    return FailedOrder(
        order_id=oid, buyer_id=buyer, amount=amount, category="book", failed_at=AT, reason=reason
    )


class FakeShop:
    def __init__(self, orders: list[FailedOrder]) -> None:
        self.orders = orders

    async def list_failed_orders(self, query: ShopFailedOrdersQuery) -> ShopFailedOrdersResult:
        return ShopFailedOrdersResult(orders=self.orders, collected_at=AT)


ORDERS = [
    _order("o1", "b1", 10_000, "PG_UNAVAILABLE"),  # 장애
    _order("o2", "b2", 20_000, "PG_UNAVAILABLE"),  # 장애
    _order("o3", "b3", 5_000, "PG_TIMEOUT"),  # 장애 (시간 초과도 시스템 쪽)
    _order("o4", "b4", 7_000, "PG_DECLINED"),  # 카드 거절 → 제외
    _order("o5", "b2", 9_000, "LIMIT_EXCEEDED"),  # 한도 초과 → 제외
    _order("o6", "b5", 3_000, "pg_declined"),  # 대소문자가 달라도 같은 사유
]


def test_user_side_failures_are_split_out() -> None:
    split = split_failures(ShopFailedOrdersResult(orders=ORDERS))
    assert [o.order_id for o in split.fault] == ["o1", "o2", "o3"]
    assert split.excluded == {"카드 거절": 2, "한도 초과": 1}
    assert split.missing_reason == 0


@pytest.mark.asyncio
async def test_impact_counts_only_fault_failures_and_notes_the_excluded() -> None:
    result = await analyze_impact(WINDOW, "shop-demo", MockLLMClient(), shop=FakeShop(ORDERS))
    out = result.output
    assert (out.failed_order_count, out.affected_customer_count, out.damage_amount) == (
        3,
        3,
        35_000,
    )
    assert out.affected_customer_ids == ["b1", "b2", "b3"]  # 카드 거절만 한 b4·b5는 보상 대상 아님
    assert [c.failed_order_count for c in out.by_category] == [3]
    lines = result.evidence.data_summary.split("\n")
    assert "카드 거절 2건은 장애 피해에서 제외" in lines
    assert "한도 초과 1건은 장애 피해에서 제외" in lines


@pytest.mark.asyncio
async def test_only_user_side_failures_means_no_damage() -> None:
    declined = [_order("o1", "b1", 7_000, "PG_DECLINED")]
    result = await analyze_impact(WINDOW, "shop-demo", MockLLMClient(), shop=FakeShop(declined))
    assert (result.output.failed_order_count, result.output.damage_amount) == (0, 0)
    assert "카드 거절 1건은 장애 피해에서 제외" in result.evidence.data_summary


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["UNKNOWN", ""])
async def test_missing_reason_gives_no_numbers(reason: str) -> None:
    orders = [*ORDERS, _order("o9", "b9", 1_000, reason)]
    with pytest.raises(ShopUnavailable) as caught:
        await analyze_impact(WINDOW, "shop-demo", MockLLMClient(), shop=FakeShop(orders))
    assert caught.value.code == "NO_FAILURE_REASON"


def test_handler_answers_cannot_tell_when_a_reason_is_missing(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setattr(handlers, "get_llm", lambda: MockLLMClient())
    orders = [_order("o1", "b1", 1_000, "UNKNOWN")]
    monkeypatch.setattr(impact_skill, "get_shop_client", lambda env=None: FakeShop(orders))
    body = {
        "meta": {
            "context_id": "ctx_inc_reason",
            "token": "mock:orchestrator",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": "impact_analysis",
        "input": WINDOW.model_dump(mode="json"),
    }
    result = TestClient(build_app()).post("/a2a", json=body).json()
    assert result["status"] == "failed"
    assert result["outcome"] == "unavailable"
    assert result["output"]["code"] == "NO_FAILURE_REASON"
    assert result["output"]["message"].startswith("확인할 수 없음")
    assert "failed_order_count" not in result["output"]  # 숫자를 내지 않는다


@pytest.mark.asyncio
async def test_unseen_failure_code_gives_no_numbers_and_says_how_many() -> None:
    """장애 쪽도 사용자 쪽도 아닌 처음 보는 코드는 장애인지 알 수 없다."""
    orders = [*ORDERS, _order("o7", "b7", 4_000, "PG_RATE_LIMITED")]
    with pytest.raises(ShopUnavailable) as caught:
        await analyze_impact(WINDOW, "shop-demo", MockLLMClient(), shop=FakeShop(orders))
    assert caught.value.code == "UNKNOWN_FAILURE_CODE"
    assert "처음 보는 실패 코드 1건 (PG_RATE_LIMITED 1건)" in str(caught.value)
    split = split_failures(ShopFailedOrdersResult(orders=orders))
    assert split.unseen == {"PG_RATE_LIMITED": 1}
