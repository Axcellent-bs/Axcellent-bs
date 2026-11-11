"""복구 확인 판정 (anomaly_detection mode="recovery", #909).

조치가 끝난 시각 T부터 지금까지를 한 구간으로 센다(5분 칸 아님). 예전 5분 칸 판정은 T 뒤에
시작하는 칸이 없으면(T가 5분 경계 직후) 몇 분 동안 판정 불가였다.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas.data import AnomalyDetectionInput
from data_agent.connectors import FailureEvent, FailureEvents
from data_agent.connectors.shop_api import ShopApi, ShopApiConnector
from data_agent.detect import judge_recovery
from data_agent.metrics.judge import Settings
from data_agent.skills.anomaly_detection import detect_anomalies

pytestmark = pytest.mark.asyncio

BOUNDARY = datetime(2026, 11, 11, 1, 5, tzinfo=UTC)  # 5분 경계
METRICS = ["payment_fail_rate", "order_fail_count"]  # 인프라 복구 확인이 묻는 지표
STEP = timedelta(seconds=5)  # 5초마다 결제 1건


class FakeShop:
    """지난 7일: 5분마다 주문 12건, 장애 쪽 실패 한 시간에 1건. T 뒤 결제·실패는 시험이 정한다."""

    def __init__(self, release: datetime, *, fault_ratio: float = 0.0) -> None:
        self.release = release
        self.payments: list[datetime] = []
        self.faults: list[datetime] = []
        start = release - timedelta(days=8)
        at = start
        while at < release - timedelta(minutes=10):
            if at.minute == 7 and at.second == 0:
                self.faults.append(at)
            at += timedelta(minutes=1)
        # T 뒤: 5초마다 결제, fault_ratio만큼 장애 쪽 실패
        for n in range(60):
            moment = release + STEP * n + timedelta(seconds=2)
            self.payments.append(moment)
            if fault_ratio and n % round(1 / fault_ratio) == 0:
                self.faults.append(moment)

    async def metric_series(
        self, metric_id: str, window_from: datetime, window_to: datetime, bucket: str = "5m"
    ) -> list[tuple[datetime, float]]:
        if metric_id != "order_count":
            return []
        out, at = [], window_from.replace(minute=window_from.minute - window_from.minute % 5,
                                          second=0, microsecond=0)  # fmt: skip
        while at < window_to:
            out.append((at, 12.0))
            at += timedelta(minutes=5)
        return out

    async def failure_events(
        self, window_from: datetime, window_to: datetime, *, limit: int = 5000, **_: object
    ) -> FailureEvents:
        events = [
            FailureEvent(
                at=t,
                code="PG_UNAVAILABLE",
                customer_id="buy_0001",
                amount=1000,
                category="books",
                event_id=f"ord_{i}",
            )
            for i, t in enumerate(self.faults)
            if window_from <= t < window_to
        ]
        return FailureEvents(events[:limit])

    async def transactions_volume(self, window_from: datetime, window_to: datetime) -> list:
        return []

    async def transactions_count(self, window_from: datetime, window_to: datetime) -> int:
        return sum(1 for t in self.payments if window_from <= t < window_to)


def _release(position: str) -> datetime:
    after = position == "after"
    return BOUNDARY + timedelta(seconds=10) if after else BOUNDARY - timedelta(seconds=20)


@pytest.mark.parametrize("position", ["after", "before"])  # 5분 경계 직후·직전
async def test_recovered_at_t_plus_60_wherever_t_is(position: str) -> None:
    release = _release(position)
    shop = FakeShop(release)
    judged = await judge_recovery(
        shop, release, release + timedelta(seconds=60), METRICS, Settings()
    )
    assert [j.status for j in judged.judgements] == ["normal", "normal"]
    assert "조치 뒤 60초: 결제 12건, 장애 쪽 실패 0건" in judged.judgements[0].note


@pytest.mark.parametrize("position", ["after", "before"])
async def test_fault_still_going_is_an_anomaly(position: str) -> None:
    release = _release(position)
    shop = FakeShop(release, fault_ratio=0.8)
    judged = await judge_recovery(
        shop, release, release + timedelta(seconds=60), METRICS, Settings()
    )
    assert {j.status for j in judged.judgements} == {"anomaly"}
    assert "장애 쪽 실패가 평소보다 많음" in judged.judgements[0].note


async def test_too_few_payments_is_unknown_for_now() -> None:
    release = _release("after")
    shop = FakeShop(release)
    judged = await judge_recovery(
        shop, release, release + timedelta(seconds=15), METRICS, Settings()
    )
    assert {j.status for j in judged.judgements} == {"unknown"}
    assert "결제 5건 미만이라 아직 판단하지 못함" in judged.judgements[0].note


async def test_one_usual_fault_failure_still_counts_as_recovered() -> None:
    """평소에도 가끔 나는 장애 쪽 실패 1건은 복구를 막지 않는다."""
    release = _release("after")
    shop = FakeShop(release)
    shop.faults.append(release + timedelta(seconds=30))
    judged = await judge_recovery(
        shop, release, release + timedelta(seconds=60), METRICS, Settings()
    )
    assert {j.status for j in judged.judgements} == {"normal"}


async def test_payments_before_t_are_not_counted() -> None:
    """조치 전(같은 5분 칸 앞부분) 결제·실패는 세지 않는다."""
    release = _release("after")
    shop = FakeShop(release)
    shop.payments += [release - timedelta(seconds=s) for s in range(1, 9)]
    shop.faults += [release - timedelta(seconds=s) for s in range(1, 9)]
    judged = await judge_recovery(
        shop, release, release + timedelta(seconds=15), METRICS, Settings()
    )
    assert {j.status for j in judged.judgements} == {"unknown"}  # T 뒤 결제는 3건뿐
    assert "장애 쪽 실패 0건" in judged.judgements[0].note


async def test_no_history_is_unknown() -> None:
    release = _release("after")
    shop = FakeShop(release)

    async def no_orders(*_: object, **__: object) -> list:
        return []

    shop.metric_series = no_orders  # type: ignore[method-assign]
    judged = await judge_recovery(
        shop, release, release + timedelta(seconds=60), METRICS, Settings()
    )
    assert {j.status for j in judged.judgements} == {"unknown"}
    assert "평소 장애 실패 수준을 모름" in judged.judgements[0].note


async def test_detect_anomalies_uses_the_recovery_judgement_for_mode_recovery() -> None:
    release = _release("after")
    data = AnomalyDetectionInput(
        window_from=release,
        window_to=release + timedelta(seconds=60),
        metrics=METRICS,
        mode="recovery",
    )
    result = await detect_anomalies(data, FakeShop(release), MockLLMClient(responses={}))
    assert result.output.status == "normal"
    assert result.output.unknown_metrics == []
    assert "판정 설정(복구 확인)" in result.evidence.data_summary

    window = data.model_copy(update={"mode": "window"})
    old = await detect_anomalies(window, FakeShop(release), MockLLMClient(responses={}))
    assert old.output.status == "unknown"  # 예전 5분 칸 판정: T 뒤에 시작하는 칸이 없다


async def test_shop_connector_counts_payments_by_their_own_time() -> None:
    release = _release("after")
    end = release + timedelta(seconds=60)

    def iso(t: datetime) -> str:
        return t.strftime("%Y-%m-%dT%H:%M:%SZ")

    orders = [
        {"order_id": "ord_1", "status": "paid", "created_at": iso(release),
         "payments": [{"amount": 1000, "status": "succeeded",
                       "created_at": iso(release + timedelta(seconds=1))}]},
        {"order_id": "ord_2", "status": "payment_failed", "created_at": iso(release),
         "payments": [{"amount": 1000, "status": "failed",
                       "created_at": iso(release + timedelta(seconds=5))},
                      {"amount": 1000, "status": "succeeded", "created_at": iso(end)}]},
    ]  # fmt: skip

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/shop/orders"
        return httpx.Response(200, json={"data": orders, "meta": {"next_cursor": None}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connector = ShopApiConnector(ShopApi("http://shop.test", token="mock:data", client=client))
    assert await connector.transactions_count(release, end) == 2  # 구간 끝(end) 결제는 빠진다
