"""구간 판정: anomaly_detection skill과 주기 감시(monitor)가 같이 쓰는 판정 함수.

두 곳이 같은 함수(`judge_window`)를 불러 규칙이 갈라지지 않게 한다. 숫자·판정은 코드
(`metrics.judge`)만 만들고 LLM을 쓰지 않는다. 문장(LLM 설명)·캠페인 조회는 skill이 붙인다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

from ax_contracts.data_access.shop_failed_orders import FailedOrder
from data_agent.metrics.anomaly import Agg
from data_agent.metrics.judge import (
    BASELINE_DAYS,
    RATE_METRICS,
    Judgement,
    Settings,
    empty_window,
    fault_buckets,
    judge_count,
    judge_payment,
    to_buckets,
    window_buckets,
)
from data_agent.metrics.judge import _unknown as unknown_judgement

Point = tuple[datetime, float]

# (지표, 예전 5분 버킷 집계 방식). 지표 목록은 skill 기본값과 감시가 같이 쓴다
WATCHED_METRICS: list[tuple[str, Agg]] = [
    ("payment_fail_rate", "mean"),
    ("order_fail_count", "sum"),
    ("order_count", "sum"),
    ("inquiry_count", "sum"),
]
EMPTY_WINDOW = "구간에 주문·결제 기록이 하나도 없음 (자료가 빠졌는지 정말 0건인지 알 수 없음)"


class MetricSource(Protocol):
    """지표 시계열 공급자 (shop-api `/api/shop/metric-series` 또는 목)."""

    async def fetch(
        self, metric: str, window_from: datetime, window_to: datetime
    ) -> list[Point]: ...


@runtime_checkable
class FaultFailureSource(Protocol):
    """실패 주문(실패 코드 포함)도 주는 공급자. 장애 쪽 실패 절대 기준에 쓴다.

    이 메서드가 없는 공급자(목 등)나 None을 준 경우(읽지 못함)는 통계 판정만 한다.
    """

    async def failed_orders(
        self, window_from: datetime, window_to: datetime
    ) -> list[FailedOrder] | None: ...


@dataclass(frozen=True)
class WindowJudgement:
    judgements: list[Judgement]
    empty: bool  # 구간에 주문·결제 기록이 하나도 없음
    queried: list[str]  # 읽은 지표 (근거용)
    fault_codes_read: bool  # 장애 쪽 실패 건수를 읽었는지

    @property
    def status(self) -> str:
        if any(j.status == "anomaly" for j in self.judgements):
            return "anomaly"
        if all(j.status == "unknown" for j in self.judgements):
            return "unknown"
        return "normal"


def _floor_5m(ts: datetime) -> datetime:
    ts = ts.astimezone(UTC)
    return ts.replace(minute=ts.minute - ts.minute % 5, second=0, microsecond=0)


async def _series(source: MetricSource, metric: str, start: datetime, end: datetime) -> dict:
    try:
        return to_buckets(await source.fetch(metric, start, end))
    except ValueError:  # 이 공급자에 없는 지표(목 공급자 등): 자료 없음
        return {}


async def _faults(
    source: MetricSource, window_from: datetime, window_to: datetime
) -> dict[datetime, float] | None:
    if not isinstance(source, FaultFailureSource):
        return None
    orders = await source.failed_orders(window_from, window_to)
    return None if orders is None else fault_buckets(orders)


async def judge_window(
    source: MetricSource,
    window_from: datetime,
    window_to: datetime,
    metrics: list[str] | None,
    settings: Settings,
) -> WindowJudgement:
    """구간 [window_from, window_to)를 지난 7일 같은 시간대와 비교해 지표별 판정을 낸다."""
    metrics = list(dict.fromkeys(metrics or [])) or [name for name, _ in WATCHED_METRICS]
    start = _floor_5m(window_from)
    history_from = start - timedelta(days=BASELINE_DAYS)
    needed = list(dict.fromkeys([*metrics, "order_count", "payment_count"]))
    series = {m: await _series(source, m, history_from, window_to) for m in needed}
    window = window_buckets(window_from, window_to)
    faults = await _faults(source, start, window_to) if RATE_METRICS & set(metrics) else None

    empty = empty_window(window, series["order_count"], series["payment_count"])
    orders = judge_count("order_count", window, series["order_count"], settings)
    volume = 1.0
    if orders.status != "unknown" and orders.baseline > 0:
        volume = max(1.0, orders.observed / orders.baseline)

    judgements: list[Judgement] = []
    for metric in metrics:
        if empty:
            judgements.append(unknown_judgement(metric, EMPTY_WINDOW))
        elif metric in RATE_METRICS:
            judgements.append(
                judge_payment(
                    window,
                    series[metric],
                    series["payment_count"],
                    faults,
                    settings,
                    orders=series["order_count"],
                )
            )
        elif metric == "order_count":
            judgements.append(orders)
        elif metric == "order_fail_count":
            judgements.append(
                judge_count(metric, window, series[metric], settings, volume_factor=volume)
            )
        else:
            judgements.append(judge_count(metric, window, series[metric], settings))
    return WindowJudgement(
        judgements=judgements,
        empty=empty,
        queried=needed,
        fault_codes_read=faults is not None,
    )
