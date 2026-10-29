"""장애 쪽 실패 절대 기준 (거래가 적은 시간대·약한 장애, PR #64 후속).

- 실패 코드 분류는 영향 분석과 같다 (`impact.split_failures`)
- 5분 구간 하나의 장애 쪽 실패가 기준 이상이면 기준선·표본 수와 상관없이 이상
- 사용자 쪽 실패(카드 거절)는 이 규칙에 넣지 않는다 (세일 오탐 방지)
- 장애 쪽 실패가 없고 표본도 부족하면 "확인할 수 없음"
- skill과 주기 감시가 같은 판정 함수(`detect.judge_window`)를 쓴다
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from ax_agent_template import events
from ax_agent_template.llm import MockLLMClient
from ax_contracts.data_access.shop_failed_orders import FailedOrder
from ax_contracts.schemas.data import AnomalyDetectionInput
from ax_contracts.schemas.event import MetricAnomalyData
from data_agent import detect, impact, monitor
from data_agent.clients.shop_client import HttpMetricSource, ShopApi
from data_agent.metrics.judge import (
    DEFAULT_FAULT_FAILURES,
    Settings,
    fault_buckets,
    judge_payment,
    settings_for,
    window_buckets,
)
from data_agent.skills.anomaly_detection import detect_anomalies

START = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)  # 새벽
END = START + timedelta(minutes=30)
WINDOW = window_buckets(START, END)


def _days(per_slot: float, *, window: float | None = None) -> dict[datetime, float]:
    values = {}
    for slot in WINDOW:
        for day in range(1, 8):
            values[slot - timedelta(days=day)] = per_slot
        values[slot] = per_slot if window is None else window
    return values


def _failed(at: datetime, reason: str, n: int = 1) -> list[FailedOrder]:
    return [
        FailedOrder(
            order_id=f"ord_{reason}_{at:%H%M}_{i}",
            buyer_id=f"buy_{i}",
            amount=10000,
            category="book",
            failed_at=at + timedelta(seconds=30 * i),
            reason=reason,
        )
        for i in range(n)
    ]


def test_fault_buckets_use_impact_classification() -> None:
    orders = (
        _failed(WINDOW[1], "PG_UNAVAILABLE", 2)
        + _failed(WINDOW[1], "PG_DECLINED", 3)  # 사용자 쪽: 세지 않는다
        + _failed(WINDOW[2], "PG_RATE_LIMITED")  # 처음 보는 코드: 세지 않는다
        + _failed(WINDOW[3], "UNKNOWN")  # 사유 없음: 세지 않는다
    )
    assert fault_buckets(orders) == {WINDOW[1]: 2.0}
    assert "PG_UNAVAILABLE" in impact.FAULT_REASONS  # 같은 분류를 쓴다
    assert "PG_DECLINED" in impact.USER_SIDE_REASONS


def test_night_fault_is_anomaly_even_below_min_payments() -> None:
    payments = _days(1.0)  # 새벽: 5분 결제 1건 (표본 부족)
    rate = _days(0.0, window=1.0)  # 기준선 0, 구간은 전부 실패
    faults = fault_buckets(_failed(WINDOW[3], "PG_UNAVAILABLE"))
    statistical = judge_payment(WINDOW, rate, payments, {}, Settings())
    assert statistical.status == "unknown"  # 장애 쪽 실패가 없으면 확인할 수 없음
    j = judge_payment(WINDOW, rate, payments, faults, Settings())
    assert j.status == "anomaly" and j.direction == "up"
    assert "장애 쪽 실패 1건" in j.note and "이상" in j.note
    assert (j.peak_ts, j.peak_to) == (WINDOW[3], WINDOW[3] + timedelta(minutes=5))


def test_declines_alone_never_trigger_the_rule() -> None:
    payments = _days(1.0)
    rate = _days(0.0, window=1.0)
    faults = fault_buckets(_failed(WINDOW[2], "PG_DECLINED", 5))  # 세일 중 카드 거절이 몰려도
    j = judge_payment(WINDOW, rate, payments, faults, Settings())
    assert j.status == "unknown"
    assert "장애 쪽 실패 0건" in j.note


def test_threshold_is_per_company() -> None:
    payments = _days(1.0)
    rate = _days(0.0, window=1.0)
    faults = fault_buckets(_failed(WINDOW[2], "PG_TIMEOUT", 2))
    assert judge_payment(WINDOW, rate, payments, faults, Settings(fault_failures=3)).status == (
        "unknown"
    )
    assert judge_payment(WINDOW, rate, payments, faults, Settings(fault_failures=2)).status == (
        "anomaly"
    )
    env = {
        "DATA_ANOMALY_FAULT_FAILURES": "4",
        "DATA_ANOMALY_SETTINGS": "big=20:0.0005:10;s=8:0.001",
    }
    assert settings_for("other", env).fault_failures == 4
    assert settings_for("big", env) == Settings(min_payments=20, alpha=0.0005, fault_failures=10)
    assert settings_for("s", env).fault_failures == 4  # 세 번째 값이 없으면 기본값
    assert settings_for(None, {}).fault_failures == DEFAULT_FAULT_FAILURES


def test_unreadable_fault_codes_fall_back_to_statistics() -> None:
    payments = _days(1.0)
    rate = _days(0.0, window=1.0)
    j = judge_payment(WINDOW, rate, payments, None, Settings())
    assert j.status == "unknown"
    assert "실패 코드를 읽지 못해" in j.note


# ---------- 공급자: shop-api 실패 주문을 읽는다 ----------


class _Shop:
    """shop-api `/metric-series`·`/failed-orders` 흉내. 새벽 결제 1건/5분, 기준 실패 0."""

    def __init__(self, failed: list[FailedOrder], *, failed_status: int = 200) -> None:
        self.failed = failed
        self.failed_status = failed_status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        frm = datetime.fromisoformat(params["from"])
        to = datetime.fromisoformat(params["to"])
        if request.url.path.endswith("/failed-orders"):
            if self.failed_status != 200:
                return httpx.Response(self.failed_status, json={"error": {"code": "FORBIDDEN"}})
            orders = [o.model_dump(mode="json") for o in self.failed if frm <= o.failed_at < to]
            return httpx.Response(200, json={"data": {"orders": orders}, "meta": {}})
        metric = params["metric"]
        points, ts = [], frm
        while ts < to:
            inside = START <= ts < END
            value = {
                "payment_fail_rate": 1.0 if inside and self.failed else 0.0,
                "payment_count": 1.0,
                "order_count": 1.0,
                "order_fail_count": 1.0 if inside and self.failed else 0.0,
            }[metric]
            points.append({"ts": ts.isoformat(), "value": value})
            ts += timedelta(minutes=5)
        data = {"metric": metric, "bucket": "5m", "points": points}
        return httpx.Response(200, json={"data": data, "meta": {}})


def _source(shop: _Shop) -> HttpMetricSource:
    client = httpx.AsyncClient(transport=httpx.MockTransport(shop), base_url="http://shop")
    return HttpMetricSource(ShopApi("http://shop", token="t", client=client))


def test_skill_flags_night_fault_from_fault_codes() -> None:
    source = _source(_Shop(_failed(START + timedelta(minutes=10), "PG_UNAVAILABLE")))
    data = AnomalyDetectionInput(window_from=START, window_to=END, metrics=["payment_fail_rate"])
    result = asyncio.run(detect_anomalies(data, source, MockLLMClient(responses={})))
    assert result.output.status == "anomaly"
    assert result.output.verdicts[0].is_anomaly is True
    assert "장애 쪽 실패 5분 1건 이상이면 이상" in result.evidence.data_summary


def test_skill_without_fault_codes_stays_unknown() -> None:
    shop = _Shop(_failed(START + timedelta(minutes=10), "PG_UNAVAILABLE"), failed_status=403)
    data = AnomalyDetectionInput(window_from=START, window_to=END, metrics=["payment_fail_rate"])
    result = asyncio.run(detect_anomalies(data, _source(shop), MockLLMClient(responses={})))
    assert result.output.verdicts[0].status == "unknown"
    assert "실패 코드를 읽지 못해 이 규칙은 쓰지 않음" in result.evidence.data_summary


# ---------- 주기 감시가 skill과 같은 판정을 쓴다 ----------


@pytest.fixture
def bus(monkeypatch):
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.delenv("DATA_ANOMALY_FAULT_FAILURES", raising=False)
    events.reset_mock_bus()
    monitor.reset_published()
    yield
    events.reset_mock_bus()
    monitor.reset_published()


def test_monitor_uses_same_judgement_as_skill(bus) -> None:
    source = _source(_Shop(_failed(START + timedelta(minutes=10), "PG_UNAVAILABLE")))
    judged = asyncio.run(
        detect.judge_window(source, START, END, [m for m, _ in detect.WATCHED_METRICS], Settings())
    )
    flagged = {j.metric for j in judged.judgements if j.status == "anomaly"}
    out = asyncio.run(monitor.run_once(source, END))
    assert {MetricAnomalyData.model_validate(e.data).metric for e in out} == flagged
    assert "payment_fail_rate" in flagged
    data = MetricAnomalyData.model_validate(
        next(e for e in out if e.data["metric"] == "payment_fail_rate").data
    )
    assert data.window_from == START + timedelta(minutes=10)
    # 같은 장애가 이어지면 다시 발행하지 않는다
    assert asyncio.run(monitor.run_once(source, END + timedelta(minutes=5))) == []


def test_monitor_stays_quiet_on_declines_only(bus) -> None:
    source = _source(_Shop(_failed(START + timedelta(minutes=10), "PG_DECLINED", 3)))
    out = asyncio.run(monitor.run_once(source, END))
    assert all(e.data["metric"] != "payment_fail_rate" for e in out)
