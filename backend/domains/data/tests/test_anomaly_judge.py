"""이상 탐지 판정 (정확도 평가 PR #55에서 나온 문제).

- 최소 표본: 5분 결제가 기준 미만이면 실패율을 판정하지 않는다 (회사별 설정)
- 비율·건수 분리: 주문 증가만으로는 이상이 아니고, 캠페인 정보는 근거에 남긴다
- 감소 방향 (주문 급감)
- 기준선 0 → "확인할 수 없음" (이상 없음이 아님)
- 빈 구간 → "확인할 수 없음"
"""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime, timedelta

from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas.data import AnomalyDetectionInput
from data_agent.metrics.judge import (
    Settings,
    binom_upper,
    judge_count,
    judge_rate,
    poisson_lower,
    poisson_upper,
    segments,
    settings_for,
    window_buckets,
)
from data_agent.skills.anomaly_detection import detect_anomalies

START = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
END = START + timedelta(minutes=30)
WINDOW = window_buckets(START, END)


def _days(per_slot: float, *, window: float | None = None) -> dict[datetime, float]:
    """지난 7일 같은 시각마다 `per_slot`, 구간 안은 `window`(없으면 per_slot)."""
    values = {}
    for slot in WINDOW:
        for day in range(1, 8):
            values[slot - timedelta(days=day)] = per_slot
        values[slot] = per_slot if window is None else window
    return values


def test_probabilities() -> None:
    assert abs(poisson_upper(1, 1.0) - (1 - math.exp(-1))) < 1e-12
    assert abs(poisson_lower(0, 2.0) - math.exp(-2)) < 1e-12
    assert abs(binom_upper(1, 2, 0.5) - 0.75) < 1e-12
    assert poisson_upper(0, 3.0) == 1.0


def test_segments_cover_whole_prefixes_and_suffixes() -> None:
    assert segments(3) == [(0, 0), (0, 1), (0, 2), (1, 2), (2, 2)]


def test_rate_is_not_judged_below_min_payments() -> None:
    payments = _days(20.0, window=3.0)  # 구간 안 5분 결제 3건
    rate = _days(0.02, window=1.0)  # 전부 실패해도
    j = judge_rate(WINDOW, rate, payments, Settings(min_payments=5))
    assert j.status == "unknown"
    assert "5분 결제가 5건 미만" in j.note
    # 회사 설정으로 기준을 낮추면 판정한다
    assert judge_rate(WINDOW, rate, payments, Settings(min_payments=3)).status == "anomaly"


def test_one_decline_in_a_busy_bucket_is_not_an_anomaly() -> None:
    payments = _days(8.0)
    rate = _days(0.02)
    rate[WINDOW[2]] = 1 / 8  # 카드 거절 1건 (예전 규칙은 6배라 이상이었다)
    assert judge_rate(WINDOW, rate, payments, Settings()).status == "normal"


def test_zero_baseline_is_unknown_not_normal() -> None:
    payments = _days(10.0)
    rate = _days(0.0, window=0.5)
    j = judge_rate(WINDOW, rate, payments, Settings())
    assert j.status == "unknown"
    assert "기준선 0" in j.note
    count = judge_count("order_fail_count", WINDOW, _days(0.0, window=3.0), Settings())
    assert count.status == "unknown" and "기준선 0" in count.note


def test_order_drop_is_detected_and_rise_is_not_an_anomaly() -> None:
    drop = judge_count("order_count", WINDOW, _days(20.0, window=4.0), Settings())
    assert (drop.status, drop.direction) == ("anomaly", "down")
    rise = judge_count("order_count", WINDOW, _days(20.0, window=60.0), Settings())
    assert rise.status == "normal"
    assert any("증가만으로는 이상이 아님" in n for n in rise.notes)


def test_failures_scale_with_order_volume() -> None:
    fails = _days(1.0, window=3.0)  # 실패 3배
    assert judge_count("order_fail_count", WINDOW, fails, Settings()).status == "anomaly"
    scaled = judge_count("order_fail_count", WINDOW, fails, Settings(), volume_factor=3.0)
    assert scaled.status == "normal"  # 주문도 3배면 정상


def test_settings_per_company() -> None:
    env = {"DATA_ANOMALY_MIN_PAYMENTS": "8", "DATA_ANOMALY_SETTINGS": "big=20:0.0005;bad=x"}
    assert settings_for("shop-demo", env) == Settings(min_payments=8, alpha=0.001)
    assert settings_for("big", env) == Settings(min_payments=20, alpha=0.0005)
    assert settings_for("bad", env) == Settings(min_payments=8, alpha=0.001)  # 틀린 값은 기본
    assert settings_for(None, {}) == Settings()


class Source:
    def __init__(self, series: dict[str, dict[datetime, float]]) -> None:
        self.series = series

    async def fetch(self, metric: str, window_from: datetime, window_to: datetime):  # noqa: ANN201
        return sorted(self.series.get(metric, {}).items())


def _detect(series: dict[str, dict[datetime, float]], campaigns=None):  # noqa: ANN001, ANN202
    data = AnomalyDetectionInput(window_from=START, window_to=END)
    return asyncio.run(detect_anomalies(data, Source(series), MockLLMClient(), campaigns=campaigns))


def test_empty_window_answers_cannot_tell() -> None:
    series = {
        "order_count": _days(20.0, window=0.0),
        "payment_count": _days(20.0, window=0.0),
        "payment_fail_rate": _days(0.02, window=0.0),
        "order_fail_count": _days(1.0, window=0.0),
    }
    out = _detect(series).output
    assert out.status == "unknown"
    assert out.any_anomaly is False
    assert out.summary.startswith("확인할 수 없음")
    assert all(v.status == "unknown" for v in out.verdicts)  # 주문 0건을 급감으로 보지 않는다


def test_sale_with_campaign_is_normal_and_campaign_goes_to_evidence() -> None:
    series = {
        "order_count": _days(20.0, window=60.0),
        "payment_count": _days(20.0, window=60.0),
        "payment_fail_rate": _days(0.02),
        "order_fail_count": _days(0.4, window=1.2),
        "inquiry_count": _days(5.0),
    }

    async def campaigns(start: datetime, end: datetime) -> list[str]:
        return ["공지 게시 2026-09-01T09:55:00Z (채널 banner)"]

    result = _detect(series, campaigns)
    assert result.output.status == "normal"
    assert result.output.any_anomaly is False
    assert "게시 중 공지·캠페인: 공지 게시 2026-09-01T09:55:00Z (채널 banner)" in (
        result.evidence.data_summary
    )


def test_unknown_metric_is_listed_but_others_still_judged() -> None:
    series = {
        "order_count": _days(20.0),
        "payment_count": _days(20.0),
        "payment_fail_rate": _days(0.02),
        "order_fail_count": _days(0.4),
    }  # inquiry_count 자료 없음
    out = _detect(series).output
    assert out.status == "normal"
    assert out.unknown_metrics == ["inquiry_count"]
    assert "확인할 수 없음: inquiry_count" in out.summary
