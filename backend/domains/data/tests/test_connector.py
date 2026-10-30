"""커넥터: data 엔진은 커넥터(`connectors.Connector`)만 쓴다.

- 캣북스는 `ShopApiConnector`(shop-api). 카탈로그 `connector = "shop-api"`로 고른다
- 테스트 전용 가짜 회사(냉장 물류)에 메모리 커넥터를 붙이면 이상 탐지·영향 분석이 shop-api 없이 돈다
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas.common import TENANT_ID_DEFAULT
from ax_contracts.schemas.data import ImpactAnalysisInput
from data_agent import catalog, detect
from data_agent.clients.shop_client import (
    HttpMetricSource,
    HttpShopClient,
    ShopApi,
    get_connector,
    get_shop_client,
)
from data_agent.connectors import (
    EVENTS_LIMIT,
    Connector,
    ConnectorUnavailable,
    FailureEvent,
    FailureEvents,
    as_connector,
)
from data_agent.connectors.shop_api import ShopApiConnector
from data_agent.metrics.judge import Settings
from data_agent.skills.impact_analysis import analyze_impact

FAKE = Path(__file__).resolve().parent / "catalogs" / "coldchain.toml"
CATBOOKS_WORDS = ("결제", "주문", "고객", "판매자", "쿠폰", "문의", "도서")
END = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
START = END - timedelta(minutes=30)
STEP = timedelta(minutes=5)


class MemoryConnector:
    """테스트 전용 메모리 커넥터: 구현할 메서드 3개만 있다 (shop-api 없음)."""

    def __init__(self, events: list[FailureEvent], volume: float = 10.0) -> None:
        self.events = events
        self.volume = volume
        self.calls: list[str] = []

    async def metric_series(self, metric_id, window_from, window_to, bucket="5m"):  # noqa: ANN001, ANN201
        self.calls.append(f"metric_series:{metric_id}")
        out, at = [], window_from
        while at < window_to:
            spike = metric_id == "cold_chain_breach_count" and at >= START
            out.append((at, 20.0 if spike else 2.0))
            at += STEP
        return out

    async def failure_events(
        self,
        window_from,  # noqa: ANN001
        window_to,  # noqa: ANN001
        *,
        category=None,  # noqa: ANN001
        limit=EVENTS_LIMIT,  # noqa: ANN001
        tenant_id=TENANT_ID_DEFAULT,  # noqa: ANN001
    ) -> FailureEvents:
        self.calls.append("failure_events")
        picked = [
            e
            for e in self.events
            if window_from <= e.at <= window_to and (category is None or e.category == category)
        ]
        return FailureEvents(picked[:limit], truncated=len(picked) > limit)

    async def transactions_volume(self, window_from, window_to):  # noqa: ANN001, ANN201
        self.calls.append("transactions_volume")
        out, at = [], window_from
        while at < window_to:
            out.append((at, self.volume))
            at += STEP
        return out


@pytest.fixture
def coldchain():
    catalog.use_catalog(catalog.load_catalog(str(FAKE)))
    yield catalog.current()
    catalog.use_catalog(None)


def _event(minute: int, code: str, customer: str, amount: int, category: str = "frozen"):
    return FailureEvent(
        at=START + timedelta(minutes=minute),
        code=code,
        customer_id=customer,
        amount=amount,
        category=category,
        event_id=f"shp_{minute}",
    )


EVENTS = [
    _event(1, "REEFER_FAILURE", "acct_a", 120000),
    _event(3, "REEFER_FAILURE", "acct_b", 80000, "chilled"),
    _event(7, "DOOR_LEFT_OPEN", "acct_a", 50000),
    # 지난 7일 같은 시각: 평소에도 하루 한 번 냉장고 고장 (평소 실패율 λ)
    *[
        _event(-day * 24 * 60 + 2, "REEFER_FAILURE", f"acct_past_{day}", 10000)
        for day in range(1, 8)
    ],
]


# ------------------------------------------------------------------ 인터페이스
def test_connectors_follow_the_interface() -> None:
    api = ShopApi("http://shop", token="t")
    assert isinstance(ShopApiConnector(api), Connector)
    assert isinstance(HttpMetricSource(api), ShopApiConnector)  # 예전 이름도 같은 커넥터
    assert isinstance(HttpShopClient(api), ShopApiConnector)
    assert isinstance(MemoryConnector([]), Connector)


def test_catalog_picks_the_connector(monkeypatch, coldchain) -> None:
    with pytest.raises(NotImplementedError):  # 메모리 커넥터는 src에 없다 (테스트가 붙인다)
        get_connector({})
    catalog.use_catalog(None)
    monkeypatch.delenv(catalog.CATALOG_ENV, raising=False)
    assert catalog.current().connector == "shop-api"
    assert isinstance(get_connector({}), ShopApiConnector)
    assert isinstance(get_shop_client({}), ShopApiConnector)


def test_old_sources_are_wrapped_as_connectors() -> None:
    class OldSource:
        async def fetch(self, metric, a, b):  # noqa: ANN001, ANN201
            return [(a, 1.0)]

    old = OldSource()
    conn = as_connector(old)
    assert conn is as_connector(old)  # 같은 공급자는 같은 커넥터 (감시 캐시가 이어지게)
    assert asyncio.run(conn.metric_series("x", START, END)) == [(START, 1.0)]
    with pytest.raises(ConnectorUnavailable):  # 실패 이벤트를 주지 않는다
        asyncio.run(conn.failure_events(START, END))
    assert asyncio.run(conn.transactions_volume(START, END)) is None


# ---------------------------------------------------- 가짜 회사 + 메모리 커넥터
def test_fake_company_anomaly_reads_only_the_connector(coldchain) -> None:
    mem = MemoryConnector(EVENTS)
    judged = asyncio.run(detect.judge_window(mem, START, END, None, Settings()))
    by_metric = {j.metric: j for j in judged.judgements}
    assert by_metric["cold_chain_breach_count"].status == "anomaly"
    assert by_metric["shipment_delay_count"].status == "normal"
    asked = {c for c in mem.calls if c.startswith("metric_series:")}
    assert asked == {"metric_series:cold_chain_breach_count", "metric_series:shipment_delay_count"}
    assert judged.fault_codes_read  # 장애 쪽 코드가 있어 실패 이벤트도 읽는다


def test_fake_company_impact_analysis_runs_on_the_memory_connector(coldchain) -> None:
    mem = MemoryConnector(EVENTS)
    data = ImpactAnalysisInput(window_from=START, window_to=END)
    result = asyncio.run(analyze_impact(data, TENANT_ID_DEFAULT, MockLLMClient(), connector=mem))
    out = result.output
    assert out.failed_order_count == 3  # 장애 쪽 실패 이벤트
    assert out.affected_customer_count == 2 and out.affected_customer_ids == ["acct_a", "acct_b"]
    assert out.damage_amount == 250000
    assert [(c.category, c.failed_order_count) for c in out.by_category] == [
        ("chilled", 1),
        ("frozen", 2),
    ]
    # 평소: 지난 7일 같은 시각 하루 1건 → 구간 길이로 환산해 초과분을 낸다
    assert out.excess_failed_order_count is not None
    assert "transactions_volume" in mem.calls
    first_line = out.summary
    assert first_line.startswith("장애 구간 실패 출고 3건, 영향 거래처 2명, 피해 금액 250,000원")
    assert not any(w in first_line for w in CATBOOKS_WORDS)


def test_unknown_code_in_fake_company_is_not_counted(coldchain) -> None:
    """처음 보는 원인 코드가 있으면 장애 피해인지 알 수 없어 숫자를 내지 않는다 (캣북스와 같다)."""
    mem = MemoryConnector([*EVENTS, _event(9, "TRUCK_LATE", "acct_c", 1000)])
    data = ImpactAnalysisInput(window_from=START, window_to=END)
    with pytest.raises(ConnectorUnavailable) as caught:
        asyncio.run(analyze_impact(data, TENANT_ID_DEFAULT, MockLLMClient(), connector=mem))
    assert caught.value.code == "UNKNOWN_FAILURE_CODE"


def test_report_order_count_goes_through_the_connector() -> None:
    """리포트의 주문 수·결제 시도·실패도 커넥터로 읽는다 (캣북스 카탈로그)."""
    from datetime import date

    from data_agent.skills.activity_report import shop_counts

    class Shop(MemoryConnector):
        async def metric_series(self, metric_id, window_from, window_to, bucket="5m"):  # noqa: ANN001, ANN201
            self.calls.append(f"metric_series:{metric_id}")
            value = {"order_count": 3.0, "payment_fail_rate": 0.5}.get(metric_id, 0.0)
            return [(window_from, value)]

        async def transactions_volume(self, window_from, window_to):  # noqa: ANN001, ANN201
            self.calls.append("transactions_volume")
            return [(window_from, 4.0)]

    shop = Shop([])
    counts = asyncio.run(shop_counts(shop, (date(2026, 10, 21), date(2026, 10, 22))))
    assert (counts.orders, counts.attempts, counts.failed) == (3.0, 4, 2)
    assert shop.calls == [
        "metric_series:order_count",
        "transactions_volume",
        "metric_series:payment_fail_rate",
    ]
