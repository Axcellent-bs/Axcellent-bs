"""구간 판정: anomaly_detection skill과 주기 감시(monitor)가 같이 쓰는 판정 함수.

두 곳이 같은 함수(`judge_window`)를 불러 규칙이 갈라지지 않게 한다. 숫자·판정은 코드
(`metrics.judge`)만 만들고 LLM을 쓰지 않는다. 문장(LLM 설명)·캠페인 조회는 skill이 붙인다.
"""

from __future__ import annotations

import weakref
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

from ax_contracts.data_access.shop_failed_orders import FailedOrder
from data_agent import catalog
from data_agent.clients.shop_client import FAILED_ORDERS_LIMIT
from data_agent.metrics.anomaly import Agg
from data_agent.metrics.judge import (
    BASELINE_DAYS,
    FAULT_LAMBDA_SPAN,
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


def watched_metrics() -> list[tuple[str, Agg]]:
    """(지표, 5분 버킷 집계 방식). 회사 카탈로그 순서다. skill 기본값과 감시가 같이 쓴다."""
    return [(m.id, m.aggregate) for m in catalog.current().metrics]


# 예전 이름 (시작할 때의 카탈로그). 판정은 `watched_metrics()`를 쓴다
WATCHED_METRICS: list[tuple[str, Agg]] = watched_metrics()


def empty_reason() -> str:
    """구간에 거래량·분모 기록이 하나도 없을 때의 이유. 이름은 카탈로그 `volume_label`."""
    shown = catalog.current().volume_label or "거래"
    return f"구간에 {shown} 기록이 하나도 없음 (자료가 빠졌는지 정말 0건인지 알 수 없음)"


# 예전 이름 (시작할 때의 카탈로그). 캣북스: "구간에 주문·결제 기록이 하나도 없음 …"
EMPTY_WINDOW = empty_reason()


class MetricSource(Protocol):
    """지표 시계열 공급자 (shop-api `/api/shop/metric-series` 또는 목)."""

    async def fetch(
        self, metric: str, window_from: datetime, window_to: datetime
    ) -> list[Point]: ...


@runtime_checkable
class FaultFailureSource(Protocol):
    """실패 주문(실패 코드 포함)도 주는 공급자. 장애 쪽 실패 확률 기준에 쓴다.

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


# 지난 7일 실패 주문 캐시의 단위. 1시간 칸 단위로 읽어 두면, 5분마다 도는 감시가 같은 칸을
# 다시 읽지 않는다 (칸 끝이 1시간을 넘어갈 때만 날마다 1칸씩 새로 읽는다)
HISTORY_SLOT = timedelta(hours=1)


class _HistoryCache:
    """공급자 하나의 지난 날 실패 주문을 1시간 칸별로 들고 있는다.

    읽은 칸은 [칸 시작, 칸 끝) 실패 주문 전부다(0건이면 빈 목록). λ는 판정 구간보다 최소
    23시간 전 자료만 보므로, 이미 지난 시간의 실패 기록이 바뀌지 않는다고 보고 다시 읽지 않는다.
    """

    def __init__(self) -> None:
        self.slots: dict[datetime, list[FailedOrder]] = {}

    def prune(self, oldest: datetime) -> None:
        for slot in [s for s in self.slots if s < oldest]:
            del self.slots[slot]


_history_caches: weakref.WeakKeyDictionary[object, _HistoryCache] = weakref.WeakKeyDictionary()


def _cache_for(source: object) -> _HistoryCache | None:
    try:
        return _history_caches.setdefault(source, _HistoryCache())
    except TypeError:  # weakref·hash가 안 되는 공급자(테스트 가짜 등): 캐시 없이 읽는다
        return None


def _floor_slot(ts: datetime) -> datetime:
    return ts.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def _slots(a: datetime, b: datetime) -> list[datetime]:
    """[a, b)를 덮는 1시간 칸 시작 시각들."""
    out, slot = [], _floor_slot(a)
    while slot < b:
        out.append(slot)
        slot += HISTORY_SLOT
    return out


async def _read_slots(
    source: FaultFailureSource, cache: _HistoryCache, needed: list[datetime]
) -> bool | None:
    """캐시에 없는 칸을 이어진 묶음마다 한 번씩 읽어 채운다.

    하나라도 못 읽으면 False, 잘렸을 수 있으면(한도만큼 받음) 채우지 않고 None이다.
    """
    missing = [s for s in needed if s not in cache.slots]
    runs: list[list[datetime]] = []
    for slot in missing:
        if runs and runs[-1][-1] + HISTORY_SLOT == slot:
            runs[-1].append(slot)
        else:
            runs.append([slot])
    for run in runs:
        orders = await source.failed_orders(run[0], run[-1] + HISTORY_SLOT)
        if orders is None:
            return False
        if len(orders) >= FAILED_ORDERS_LIMIT:  # 잘렸을 수 있어 캐시에 넣지 않는다
            return None
        filled: dict[datetime, list[FailedOrder]] = {slot: [] for slot in run}
        for order in orders:
            slot = _floor_slot(order.failed_at)
            if slot in filled:
                filled[slot].append(order)
        cache.slots.update(filled)
    return True


async def _fault_history(
    source: MetricSource, window_from: datetime, window_to: datetime
) -> dict[datetime, float] | None:
    """지난 7일 같은 시각 ±1시간의 장애 쪽 실패 (평소 실패율 λ용). 하루라도 못 읽으면 None.

    7일을 한 번에 읽지 않고 날마다 필요한 범위만 읽는다(조회 건수 제한에 걸리지 않게).
    같은 공급자로 다시 판정하면(주기 감시) 이미 읽은 1시간 칸은 다시 읽지 않는다(`_HistoryCache`).
    날마다 [시작, 끝) 안 주문만 골라 세므로 결과는 캐시 없이 날마다 읽을 때와 같다.
    """
    if not isinstance(source, FaultFailureSource):
        return None
    cache = _cache_for(source)
    ranges = []
    for day in range(1, BASELINE_DAYS + 1):
        shift = timedelta(days=day)
        ranges.append(
            (window_from - shift - FAULT_LAMBDA_SPAN, window_to - shift + FAULT_LAMBDA_SPAN)
        )
    if cache is not None:
        cache.prune(_floor_slot(ranges[-1][0]) - timedelta(days=1))
        out: dict[datetime, float] = {}
        for a, b in ranges:
            needed = _slots(a, b)
            read = await _read_slots(source, cache, needed)
            if read is False:
                return None
            if read is None:  # 잘렸을 수 있음: 이번에는 캐시 없이 날마다 읽는다
                break
            orders = [o for slot in needed for o in cache.slots[slot] if a <= o.failed_at < b]
            out.update(fault_buckets(orders))
        else:
            return out
    out = {}
    for a, b in ranges:
        orders = await source.failed_orders(a, b)
        if orders is None:
            return None
        out.update(fault_buckets(orders))
    return out


async def judge_window(
    source: MetricSource,
    window_from: datetime,
    window_to: datetime,
    metrics: list[str] | None,
    settings: Settings,
) -> WindowJudgement:
    """구간 [window_from, window_to)를 지난 7일 같은 시간대와 비교해 지표별 판정을 낸다."""
    cat = catalog.current()
    metrics = list(dict.fromkeys(metrics or [])) or [name for name, _ in watched_metrics()]
    start = _floor_5m(window_from)
    # 거래량 지표(캣북스: 주문 수)와 비율 지표의 분모(캣북스: 결제 시도 수)를 같이 읽는다.
    # λ의 자료 유무는 거래량 시계열로 안다. 7일 전 날의 "같은 시각 − 1시간" 쪽은 이 범위 밖이라
    # 그 날은 범위 안 구간만 센다(자료가 있는 구간만 평균)
    history_from = start - timedelta(days=BASELINE_DAYS)
    volume_metric = cat.volume_metric
    rates = catalog.rate_metric_ids()
    denominators = [m.denominator for m in cat.metrics if m.denominator]
    needed = list(dict.fromkeys([*metrics, *filter(None, [volume_metric]), *denominators]))
    series = {m: await _series(source, m, history_from, window_to) for m in needed}
    window = window_buckets(window_from, window_to)
    with_faults = any((m := catalog.metric(x)) and m.fault_codes for x in metrics)
    faults = await _faults(source, start, window_to) if with_faults else None
    fault_history = await _fault_history(source, start, window_to) if faults is not None else None

    volume_series = series.get(volume_metric or "", {})
    first_denominator = series.get(denominators[0], {}) if denominators else {}
    empty = bool(volume_metric) and empty_window(window, volume_series, first_denominator)
    orders = judge_count(volume_metric, window, volume_series, settings) if volume_metric else None
    volume = 1.0
    if orders is not None and orders.status != "unknown" and orders.baseline > 0:
        volume = max(1.0, orders.observed / orders.baseline)

    judgements: list[Judgement] = []
    for metric in metrics:
        spec = catalog.metric(metric)
        if empty:
            judgements.append(unknown_judgement(metric, empty_reason()))
        elif metric in rates and spec is not None and spec.denominator:
            judgements.append(
                judge_payment(
                    window,
                    series[metric],
                    series[spec.denominator],
                    faults if spec.fault_codes else None,
                    settings,
                    orders=volume_series,
                    fault_history=fault_history,
                    metric=metric,
                )
            )
        elif orders is not None and metric == volume_metric:
            judgements.append(orders)
        elif spec is not None and spec.scale_with_volume:
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
