"""장애 쪽 실패 확률 기준 (거래가 적은 시간대·약한 장애).

- 실패 코드 분류는 영향 분석과 같다 (`impact.split_failures`)
- 평소 실패율 λ = 지난 7일 같은 시각 ±1시간 5분 구간의 장애 쪽 실패 평균 (하한 `fault_lambda_floor`)
- 5분 구간의 p = P(X ≥ 관측 | λ)가 `fault_p_anomaly`보다 작으면 이상
- 이어진 두 구간이 모두 p < `fault_p_low`이면 두 p를 곱해 다시 판정
  - 주문 시도가 0건으로 기록된 5분 하나는 건너뛴다. 자료가 없는 5분은 연속을 끊는다
  - 앞 구간(감시 구간 밖) 자료는 읽지 않는다. 그래서 구간 첫 5분은 앞과 이어졌다고 보지 않는다
- 이상이 아닌데 p < `fault_p_low`이면 통계 판정(normal·unknown)을 그대로 두고 severity low
- 지난 7일 자료가 없으면 이 기준은 판정하지 않는다 (통계 판정 그대로)
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
    DEFAULT_FAULT_LAMBDA_FLOOR,
    DEFAULT_FAULT_P_ANOMALY,
    DEFAULT_FAULT_P_LOW,
    Settings,
    fault_buckets,
    fault_lambda,
    judge_payment,
    poisson_upper,
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


def _history(window: list[datetime], per_slot: float) -> dict:
    """지난 7일 같은 시각 ±1시간 5분 구간마다 장애 쪽 실패 `per_slot`건."""
    values = {}
    for slot in window:
        for day in range(1, 8):
            for k in range(-12, 13):
                values[slot - timedelta(days=day) + FIVE * k] = per_slot
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


def _day_judge(
    faults: dict,
    orders: dict | None = None,
    *,
    history: dict | None = None,
    settings: Settings | None = None,
):  # noqa: ANN202
    """낮: 결제 20건/5분, 기준 실패율 2%, 구간도 2% (통계 판정은 normal).

    주문 자료는 지난 7일 같은 시각 ±1시간에도 있다. 장애 쪽 실패 이력은 기본 0건(λ = 하한).
    """
    w = _window(DAY)
    if orders is None:
        orders = {**_history(w, 20.0), **_days(w, 20.0)}
    return judge_payment(
        w,
        _days(w, 0.02),
        _days(w, 20.0),
        faults,
        settings or Settings(),
        orders=orders,
        fault_history={} if history is None else history,
    )


def test_defaults() -> None:
    assert (DEFAULT_FAULT_LAMBDA_FLOOR, DEFAULT_FAULT_P_ANOMALY, DEFAULT_FAULT_P_LOW) == (
        0.05,
        0.01,
        0.1,
    )
    s = Settings()
    assert (s.fault_lambda_floor, s.fault_p_anomaly, s.fault_p_low) == (0.05, 0.01, 0.1)
    # 예전 고정 기준은 읽기만 한다 (판정에 쓰지 않음)
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


# ---------- λ (평소 이 시각의 장애 쪽 실패율) ----------


def test_lambda_is_the_average_of_same_time_plus_minus_one_hour() -> None:
    w = _window(DAY)
    orders = _history(w, 20.0)
    history = {w[0] - timedelta(days=1): 7.0, w[0] - timedelta(days=3) + FIVE * 12: 7.0}
    lam, raw = fault_lambda(w[0], history, orders, floor=0.05)
    assert raw == pytest.approx(14 / (7 * 25))  # 7일 × 25구간 (±1시간, 양끝 포함)
    assert lam == raw
    # 1시간 넘게 떨어진 구간은 넣지 않는다
    far = {w[0] - timedelta(days=1) + FIVE * 13: 100.0}
    assert fault_lambda(w[0], far, orders, floor=0.05) == (0.05, 0.0)


def test_lambda_floor_is_applied() -> None:
    w = _window(DAY)
    lam, raw = fault_lambda(w[0], {}, _history(w, 20.0), floor=0.05)
    assert (lam, raw) == (0.05, 0.0)
    # 하한을 크게 하면 같은 2건도 드물지 않다 → 이상 아님
    loose = _day_judge(_faults(w[2], n=2), settings=Settings(fault_lambda_floor=0.5))
    assert loose.status == "normal" and loose.severity == "low"  # p = 0.09 < 0.1
    assert "하한 0.5 적용" in loose.note
    assert _day_judge(_faults(w[2], n=2)).status == "anomaly"  # 하한 0.05면 p = 0.0012


def test_lambda_counts_only_buckets_with_order_data() -> None:
    w = _window(DAY)
    orders = {w[0] - timedelta(days=1): 0.0}  # 주문 0건으로 기록된 5분 하나만 자료가 있다
    assert fault_lambda(w[0], {w[0] - timedelta(days=1): 1.0}, orders, floor=0.05) == (1.0, 1.0)
    assert fault_lambda(w[0], {}, {}, floor=0.05) == (None, None)


# ---------- 시간대마다 같은 건수라도 판정이 다르다 ----------


def test_two_failures_are_normal_when_this_hour_usually_fails_often() -> None:
    w = _window(DAY)
    busy = _history(w, 1.0)  # 평소 이 시각에 5분마다 장애 쪽 실패 1건 (λ = 1)
    j = _day_judge(_faults(w[2], n=2), history=busy)
    assert j.status == "normal"
    assert j.severity is None  # P(X ≥ 2 | 1) = 0.26: 드물지 않다
    assert f"p={poisson_upper(2, 1.0):.2g}" in j.note


def test_two_failures_are_an_anomaly_when_this_hour_is_usually_quiet() -> None:
    w = _window(NIGHT)
    payments = _days(w, 1.0)  # 새벽: 5분 결제 1건 (통계 판정은 표본 부족)
    orders = {**_history(w, 1.0), **payments}
    j = judge_payment(
        w,
        _days(w, 0.0, inside=1.0),
        payments,
        _faults(w[3], n=2),
        Settings(),
        orders=orders,
        fault_history={},  # 평소 장애 쪽 실패 0건 → λ = 하한 0.05
    )
    assert j.status == "anomaly" and j.direction == "up"
    assert (j.peak_ts, j.peak_to) == (w[3], w[3] + FIVE)
    assert poisson_upper(2, 0.05) < DEFAULT_FAULT_P_ANOMALY


def test_no_seven_day_data_is_unknown() -> None:
    w = _window(NIGHT)
    payments = {b: 1.0 for b in w}  # 구간 안 자료만 있고 지난 7일은 없다
    j = judge_payment(
        w,
        {b: 1.0 for b in w},
        payments,
        _faults(w[3], n=3),
        Settings(),
        orders=payments,
        fault_history={},
    )
    assert j.status == "unknown"  # 3건이어도 평소를 모르면 이상이라고 하지 않는다
    assert j.severity == "low"
    assert "평소 실패율을 정할 수 없음" in j.note
    # 지난 7일 장애 쪽 실패를 읽지 못하면(None) 이 기준은 판정하지 않고 통계 판정을 그대로 둔다
    d = _window(DAY)
    unread = judge_payment(
        d, _days(d, 0.02), _days(d, 20.0), _faults(d[3], n=3), Settings(), orders=_days(d, 20.0)
    )
    assert unread.status == "normal" and unread.severity == "low"
    assert "평소 실패율을 정할 수 없음" in unread.note
    # 이력을 읽었고 0건이면({}) λ = 하한으로 판정한다
    assert _day_judge(_faults(d[3], n=3)).status == "anomaly"


# ---------- 이어진 두 구간: 확률을 곱한다 ----------


def test_one_failure_in_one_bucket_is_normal_with_low_severity() -> None:
    w = _window(DAY)
    j = _day_judge(_faults(w[2]))
    assert j.status == "normal"
    assert j.severity == "low"  # p = 0.049 < 0.1
    assert "장애 쪽 실패 1건, 드물지만 이상 아님" in j.note


def test_two_consecutive_rare_buckets_multiply() -> None:
    w = _window(DAY)
    j = _day_judge(_faults(w[2], w[3]))
    assert j.status == "anomaly" and j.direction == "up"
    assert (j.peak_ts, j.peak_to) == (w[2], w[3] + FIVE)
    joint = poisson_upper(1, 0.05) ** 2
    assert joint < DEFAULT_FAULT_P_ANOMALY < poisson_upper(1, 0.05)
    assert f"p={joint:.2g}" in j.note
    # 곱해도 기준보다 크면 이상이 아니다 (low 기록)
    strict = _day_judge(_faults(w[2], w[3]), settings=Settings(fault_p_anomaly=0.001))
    assert strict.status == "normal" and strict.severity == "low"
    # 한 구간이 p_low보다 흔하면 곱하지 않는다
    lenient = _day_judge(_faults(w[2], w[3]), settings=Settings(fault_p_low=0.04))
    assert lenient.status == "normal" and lenient.severity is None


def test_missing_previous_bucket_is_not_consecutive() -> None:
    w = _window(DAY)
    # 감시 구간 첫 5분: 그 앞 5분은 읽지 않으므로 앞과 이어졌다고 보지 않는다
    before = w[0] - FIVE
    first = _day_judge(fault_buckets(_failed(before) + _failed(w[0])))
    assert first.status == "normal" and first.severity == "low"
    # 사이 5분의 주문 자료가 없으면(조회 실패·자료 없음) 건너뛰지 않고 연속을 끊는다
    orders = {**_history(w, 20.0), **_days(w, 20.0)}
    del orders[w[2]]
    gap = _day_judge(_faults(w[1], w[3]), orders)
    assert gap.status == "normal" and gap.severity == "low"


def test_one_zero_order_bucket_is_skipped_but_not_two() -> None:
    w = _window(DAY)
    orders = {**_history(w, 20.0), **_days(w, 20.0)}
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
    j = judge_payment(
        w,
        rate,
        payments,
        _faults(w[3]),
        Settings(),
        orders={**_history(w, 1.0), **payments},
        fault_history={},
    )
    assert j.status == "unknown"  # normal로 덮지 않는다 (기존 판정 그대로)
    assert j.severity == "low"
    assert "장애 쪽 실패 1건, 드물지만 이상 아님" in j.note


def test_declines_alone_never_trigger_the_rule() -> None:
    w = _window(NIGHT)
    payments = _days(w, 1.0)
    faults = fault_buckets(_failed(w[2], "PG_DECLINED", 5))  # 세일 중 카드 거절이 몰려도
    j = judge_payment(
        w,
        _days(w, 0.0, inside=1.0),
        payments,
        faults,
        Settings(),
        orders={**_history(w, 1.0), **payments},
        fault_history={},
    )
    assert j.status == "unknown" and j.severity is None
    assert "장애 쪽 실패 0건" in j.note


def test_thresholds_are_per_company_and_from_env() -> None:
    env = {
        "DATA_FAULT_LAMBDA_FLOOR": "0.2",
        "DATA_FAULT_P_ANOMALY": "0.001",
        "DATA_FAULT_P_LOW": "0.05",
        "DATA_ANOMALY_FAULT_FAILURES": "4",
        "DATA_ANOMALY_FAULT_CONSECUTIVE": "3",
        "DATA_ANOMALY_SETTINGS": "big=20:0.0005:10:5;s=8:0.001",
    }
    other = settings_for("other", env)
    assert (other.fault_lambda_floor, other.fault_p_anomaly, other.fault_p_low) == (
        0.2,
        0.001,
        0.05,
    )
    assert (other.fault_failures, other.fault_consecutive) == (4, 3)  # 읽기만 한다
    big = settings_for("big", env)
    assert (big.min_payments, big.alpha, big.fault_failures, big.fault_consecutive) == (
        20,
        0.0005,
        10,
        5,
    )
    assert big.fault_p_anomaly == 0.001  # 회사별 덮어쓰기에 없는 값은 공통 기본값
    assert settings_for("s", env).fault_consecutive == 3  # 네 번째 값이 없으면 기본값
    assert settings_for(None, {}) == Settings()
    # 형식이 틀리거나 범위 밖이면 기본값
    bad = settings_for(None, {"DATA_FAULT_LAMBDA_FLOOR": "-1", "DATA_FAULT_P_ANOMALY": "2"})
    assert (bad.fault_lambda_floor, bad.fault_p_anomaly) == (0.05, 0.01)
    # 같은 2건이라도 기준값에 따라 판정이 달라진다
    w = _window(DAY)
    assert _day_judge(_faults(w[2], n=2), settings=other).status == "normal"  # λ 0.2: p=0.018
    assert _day_judge(_faults(w[2], n=2)).status == "anomaly"


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
    assert "이어진 두 구간은 곱" in result.evidence.data_summary


def test_skill_records_single_failure_as_low_without_incident() -> None:
    result = _skill(_source(_Shop(SINGLE)))
    verdict = result.output.verdicts[0]
    assert result.output.any_anomaly is False
    assert (verdict.status, verdict.severity) == ("normal", "low")
    assert "장애 쪽 실패 1건, 드물지만 이상 아님" in (verdict.note or "")


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
