"""장애 쪽 실패 단계별 기준 (거래가 적은 시간대·약한 장애).

- 실패 코드 분류는 영향 분석과 같다 (`impact.split_failures`)
- 5분 구간 하나에 장애 쪽 실패가 `fault_failures`(기본 2)건 이상이면 이상
- 장애 쪽 실패 1건 이상인 5분 구간이 `fault_consecutive`(기본 2)개 연속이면 이상
  - 주문 시도가 0건으로 기록된 5분 하나는 건너뛴다. 자료가 없는 5분은 연속을 끊는다
  - 앞 구간(감시 구간 밖) 자료는 읽지 않는다. 그래서 구간 첫 5분은 앞과 이어졌다고 보지 않는다
- 1건이 한 구간만 나면 이상이 아니다. 통계 판정(normal·unknown)을 그대로 두고 severity low와
  "장애 쪽 실패 1건, 지속 안 됨"을 남긴다
- 사용자 쪽 실패(카드 거절)는 이 규칙에 넣지 않는다 (세일 오탐 방지)
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
    DEFAULT_FAULT_CONSECUTIVE,
    DEFAULT_FAULT_FAILURES,
    Settings,
    fault_buckets,
    judge_payment,
    settings_for,
    window_buckets,
)
from data_agent.skills.anomaly_detection import detect_anomalies

FIVE = timedelta(minutes=5)
NIGHT = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)  # 새벽: 5분 결제 1건 (표본 부족)
DAY = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)  # 낮: 5분 결제 20건


def _window(start: datetime) -> list[datetime]:
    return window_buckets(start, start + timedelta(minutes=30))


def _days(window: list[datetime], per_slot: float, *, inside: float | None = None) -> dict:
    """지난 7일 같은 시각마다 `per_slot`, 구간 안은 `inside`(없으면 per_slot)."""
    values = {}
    for slot in window:
        for day in range(1, 8):
            values[slot - timedelta(days=day)] = per_slot
        values[slot] = per_slot if inside is None else inside
    return values


def _failed(at: datetime, reason: str = "PG_TIMEOUT", n: int = 1) -> list[FailedOrder]:
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


def _faults(*buckets: datetime, n: int = 1) -> dict[datetime, float]:
    return fault_buckets([o for b in buckets for o in _failed(b, n=n)])


def _day_judge(faults: dict, orders: dict | None = None):  # noqa: ANN202
    """낮: 결제 20건/5분, 기준 실패율 2%, 구간도 2% (통계 판정은 normal)."""
    w = _window(DAY)
    return judge_payment(
        w, _days(w, 0.02), _days(w, 20.0), faults, Settings(), orders=orders or _days(w, 20.0)
    )


def test_defaults() -> None:
    assert (DEFAULT_FAULT_FAILURES, DEFAULT_FAULT_CONSECUTIVE) == (2, 2)


def test_fault_buckets_use_impact_classification() -> None:
    w = _window(NIGHT)
    orders = (
        _failed(w[1], "PG_UNAVAILABLE", 2)
        + _failed(w[1], "PG_DECLINED", 3)  # 사용자 쪽: 세지 않는다
        + _failed(w[2], "PG_RATE_LIMITED")  # 처음 보는 코드: 세지 않는다
        + _failed(w[3], "UNKNOWN")  # 사유 없음: 세지 않는다
    )
    assert fault_buckets(orders) == {w[1]: 2.0}
    assert "PG_UNAVAILABLE" in impact.FAULT_REASONS  # 같은 분류를 쓴다
    assert "PG_DECLINED" in impact.USER_SIDE_REASONS


def test_one_failure_in_one_bucket_is_normal_with_low_severity() -> None:
    w = _window(DAY)
    j = _day_judge(_faults(w[2]))
    assert j.status == "normal"
    assert j.severity == "low"
    assert "장애 쪽 실패 1건, 지속 안 됨" in j.note


def test_one_failure_in_two_consecutive_buckets_is_anomaly() -> None:
    w = _window(DAY)
    j = _day_judge(_faults(w[2], w[3]))
    assert j.status == "anomaly" and j.direction == "up"
    assert (j.peak_ts, j.peak_to) == (w[2], w[3] + FIVE)
    assert "연속 최대 2구간" in j.note


def test_two_failures_in_one_bucket_is_anomaly() -> None:
    w = _window(DAY)
    j = _day_judge(_faults(w[4], n=2))
    assert j.status == "anomaly"
    assert (j.peak_ts, j.peak_to) == (w[4], w[4] + FIVE)


def test_missing_previous_bucket_is_not_consecutive() -> None:
    w = _window(DAY)
    # 감시 구간 첫 5분: 그 앞 5분은 읽지 않으므로 앞과 이어졌다고 보지 않는다
    before = w[0] - FIVE
    first = _day_judge(fault_buckets(_failed(before) + _failed(w[0])))
    assert first.status == "normal" and first.severity == "low"
    # 사이 5분의 주문 자료가 없으면(조회 실패·자료 없음) 건너뛰지 않고 연속을 끊는다
    orders = _days(w, 20.0)
    del orders[w[2]]
    gap = _day_judge(_faults(w[1], w[3]), orders)
    assert gap.status == "normal" and gap.severity == "low"


def test_one_zero_order_bucket_is_skipped_but_not_two() -> None:
    w = _window(DAY)
    orders = _days(w, 20.0)
    orders[w[2]] = 0.0  # 주문 시도 0건으로 기록된 5분 하나
    assert _day_judge(_faults(w[1], w[3]), orders).status == "anomaly"
    orders[w[3]] = 0.0  # 0건 5분이 둘 이어지면 건너뛰지 않는다
    two = _day_judge(_faults(w[1], w[4]), orders)
    assert two.status == "normal" and two.severity == "low"
    # 사이 5분에 주문이 있는데 장애 쪽 실패가 0건이면 연속이 끊긴다
    assert _day_judge(_faults(w[1], w[3])).status == "normal"


def test_one_failure_with_too_few_payments_stays_unknown_with_low() -> None:
    w = _window(NIGHT)
    payments = _days(w, 1.0)  # 5분 결제 1건: 통계 판정은 표본 부족으로 unknown
    rate = _days(w, 0.0, inside=1.0)
    j = judge_payment(w, rate, payments, _faults(w[3]), Settings(), orders=payments)
    assert j.status == "unknown"  # normal로 덮지 않는다 (기존 판정 그대로)
    assert j.severity == "low"
    assert "장애 쪽 실패 1건, 지속 안 됨" in j.note


def test_declines_alone_never_trigger_the_rule() -> None:
    w = _window(NIGHT)
    payments = _days(w, 1.0)
    faults = fault_buckets(_failed(w[2], "PG_DECLINED", 5))  # 세일 중 카드 거절이 몰려도
    j = judge_payment(w, _days(w, 0.0, inside=1.0), payments, faults, Settings())
    assert j.status == "unknown" and j.severity is None
    assert "장애 쪽 실패 0건" in j.note


def test_thresholds_are_per_company() -> None:
    w = _window(DAY)
    assert _day_judge(_faults(w[2], w[3], w[4])).status == "anomaly"
    three = judge_payment(
        w,
        _days(w, 0.02),
        _days(w, 20.0),
        _faults(w[2], w[3]),
        Settings(fault_consecutive=3),
        orders=_days(w, 20.0),
    )
    assert three.status == "normal" and three.severity == "low"
    env = {
        "DATA_ANOMALY_FAULT_FAILURES": "4",
        "DATA_ANOMALY_FAULT_CONSECUTIVE": "3",
        "DATA_ANOMALY_SETTINGS": "big=20:0.0005:10:5;s=8:0.001",
    }
    assert (
        settings_for("other", env).fault_failures,
        settings_for("other", env).fault_consecutive,
    ) == (
        4,
        3,
    )
    assert settings_for("big", env) == Settings(
        min_payments=20, alpha=0.0005, fault_failures=10, fault_consecutive=5
    )
    assert settings_for("s", env).fault_consecutive == 3  # 네 번째 값이 없으면 기본값
    assert settings_for(None, {}) == Settings()


def test_unreadable_fault_codes_fall_back_to_statistics() -> None:
    w = _window(NIGHT)
    j = judge_payment(w, _days(w, 0.0, inside=1.0), _days(w, 1.0), None, Settings())
    assert j.status == "unknown"
    assert "실패 코드를 읽지 못해" in j.note


# ---------- 공급자: shop-api 실패 주문을 읽는다 ----------


class _Shop:
    """shop-api `/metric-series`·`/failed-orders` 흉내. 낮: 5분 주문·결제 20건, 실패율 2%."""

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
            value = {
                "payment_fail_rate": 0.02,
                "payment_count": 20.0,
                "order_count": 20.0,
                "order_fail_count": 0.4,
            }[metric]
            points.append({"ts": ts.isoformat(), "value": value})
            ts += FIVE
        data = {"metric": metric, "bucket": "5m", "points": points}
        return httpx.Response(200, json={"data": data, "meta": {}})


def _source(shop: _Shop) -> HttpMetricSource:
    client = httpx.AsyncClient(transport=httpx.MockTransport(shop), base_url="http://shop")
    return HttpMetricSource(ShopApi("http://shop", token="t", client=client))


DAY_END = DAY + timedelta(minutes=30)
LASTING = _failed(DAY + timedelta(minutes=10)) + _failed(DAY + timedelta(minutes=15))
SINGLE = _failed(DAY + timedelta(minutes=10))


def _skill(source: HttpMetricSource):  # noqa: ANN202
    data = AnomalyDetectionInput(window_from=DAY, window_to=DAY_END, metrics=["payment_fail_rate"])
    return asyncio.run(detect_anomalies(data, source, MockLLMClient(responses={})))


def test_skill_flags_lasting_failures_from_fault_codes() -> None:
    result = _skill(_source(_Shop(LASTING)))
    assert result.output.status == "anomaly"
    assert result.output.verdicts[0].is_anomaly is True
    assert "1건 이상 2구간 연속이면 이상" in result.evidence.data_summary


def test_skill_records_single_failure_as_low_without_incident() -> None:
    result = _skill(_source(_Shop(SINGLE)))
    verdict = result.output.verdicts[0]
    assert result.output.any_anomaly is False
    assert (verdict.status, verdict.severity) == ("normal", "low")
    assert "장애 쪽 실패 1건, 지속 안 됨" in (verdict.note or "")


def test_skill_without_fault_codes_uses_statistics_only() -> None:
    result = _skill(_source(_Shop(LASTING, failed_status=403)))
    assert result.output.verdicts[0].status == "normal"
    assert "실패 코드를 읽지 못해 이 규칙은 쓰지 않음" in result.evidence.data_summary


# ---------- 주기 감시가 skill과 같은 판정을 쓴다 ----------


@pytest.fixture
def bus(monkeypatch):
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    for name in ("DATA_ANOMALY_FAULT_FAILURES", "DATA_ANOMALY_FAULT_CONSECUTIVE"):
        monkeypatch.delenv(name, raising=False)
    events.reset_mock_bus()
    monitor.reset_published()
    yield
    events.reset_mock_bus()
    monitor.reset_published()


@pytest.mark.parametrize("failed", [LASTING, SINGLE], ids=["lasting", "single"])
def test_monitor_and_skill_give_the_same_judgement(bus, failed) -> None:
    source = _source(_Shop(failed))
    metrics = [m for m, _ in detect.WATCHED_METRICS]
    judged = asyncio.run(detect.judge_window(source, DAY, DAY_END, metrics, Settings()))
    data = AnomalyDetectionInput(window_from=DAY, window_to=DAY_END, metrics=metrics)
    skill = asyncio.run(detect_anomalies(data, source, MockLLMClient(responses={})))
    out = asyncio.run(monitor.run_once(source, DAY_END, lookback=timedelta(minutes=30)))

    by_skill = {(v.metric, v.status, v.severity) for v in skill.output.verdicts}
    by_window = {(j.metric, j.status, j.severity) for j in judged.judgements}
    assert by_skill == by_window
    flagged = {v.metric for v in skill.output.verdicts if v.is_anomaly}
    assert {MetricAnomalyData.model_validate(e.data).metric for e in out} == flagged
    assert ("payment_fail_rate" in flagged) is (failed is LASTING)


def test_monitor_does_not_republish_a_lasting_fault(bus) -> None:
    source = _source(_Shop(LASTING))
    first = asyncio.run(monitor.run_once(source, DAY_END))
    assert [e.data["metric"] for e in first] == ["payment_fail_rate"]
    data = MetricAnomalyData.model_validate(first[0].data)
    assert data.window_from == DAY + timedelta(minutes=10)
    assert asyncio.run(monitor.run_once(source, DAY_END + FIVE)) == []


def test_monitor_stays_quiet_on_declines_only(bus) -> None:
    source = _source(_Shop(_failed(DAY + timedelta(minutes=10), "PG_DECLINED", 3)))
    out = asyncio.run(monitor.run_once(source, DAY_END))
    assert all(e.data["metric"] != "payment_fail_rate" for e in out)
