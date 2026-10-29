"""λ 이력 캐시 (#129): 감시 1회당 `/failed-orders` 조회를 줄이되 판정은 캐시 없을 때와 같다."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from ax_contracts.data_access.shop_failed_orders import FailedOrder
from data_agent import detect
from data_agent.clients.shop_client import HttpMetricSource, ShopApi
from data_agent.metrics.judge import Settings

FIVE = timedelta(minutes=5)
START = datetime(2026, 9, 1, 9, 40, tzinfo=UTC)
METRICS = [m for m, _ in detect.WATCHED_METRICS]


def _orders() -> list[FailedOrder]:
    """지난 8일 동안 7분 13초마다 실패 1건. 장애 쪽(PG_TIMEOUT)·사용자 쪽(PG_DECLINED) 섞음."""
    out, ts, i = [], START - timedelta(days=8), 0
    while ts < START + timedelta(hours=4):
        out.append(
            FailedOrder(
                order_id=f"ord_{i}",
                buyer_id="buy_0001",
                amount=10000,
                category="book",
                failed_at=ts,
                reason="PG_DECLINED" if i % 3 == 0 else "PG_TIMEOUT",
            )
        )
        ts += timedelta(minutes=7, seconds=13)
        i += 1
    return out


class _Shop:
    """shop-api 흉내. `/failed-orders` 조회 횟수를 센다."""

    def __init__(self, failed: list[FailedOrder]) -> None:
        self.failed = failed
        self.failed_calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        frm = datetime.fromisoformat(params["from"])
        to = datetime.fromisoformat(params["to"])
        if request.url.path.endswith("/failed-orders"):
            self.failed_calls += 1
            orders = [o.model_dump(mode="json") for o in self.failed if frm <= o.failed_at < to]
            return httpx.Response(200, json={"data": {"orders": orders}, "meta": {}})
        value = {
            "payment_fail_rate": 0.02,
            "payment_count": 20.0,
            "order_count": 20.0,
            "order_fail_count": 0.4,
        }.get(params["metric"], 0.0)
        points, ts = [], frm
        while ts < to:
            points.append({"ts": ts.isoformat(), "value": value})
            ts += FIVE
        data = {"metric": params["metric"], "bucket": "5m", "points": points}
        return httpx.Response(200, json={"data": data, "meta": {}})


def _source(shop: _Shop) -> HttpMetricSource:
    client = httpx.AsyncClient(transport=httpx.MockTransport(shop), base_url="http://shop")
    return HttpMetricSource(ShopApi("http://shop", token="t", client=client))


def _judge(source: HttpMetricSource, now: datetime) -> detect.WindowJudgement:
    return asyncio.run(
        detect.judge_window(source, now - timedelta(minutes=30), now, METRICS, Settings())
    )


@contextlib.contextmanager
def _no_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """캐시를 끈 예전 방식(날마다 [시작, 끝)을 바로 읽음)으로 계산한다. 비교 기준."""
    with monkeypatch.context() as patch:
        patch.setattr(detect, "_cache_for", lambda source: None)
        yield


def _summary(judged: detect.WindowJudgement) -> list[tuple]:
    return [
        (j.metric, j.status, j.severity, j.observed, j.baseline, j.p_value, j.note)
        for j in judged.judgements
    ]


def test_cached_history_equals_fresh_reads_every_5_minutes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3시간 동안 5분마다(초 단위 어긋난 시각 포함) 감시: 캐시 쓴 결과 = 캐시 없이 읽은 결과."""
    orders = _orders()
    cached = _source(_Shop(orders))
    now = START + timedelta(seconds=17)
    while now < START + timedelta(hours=3):
        window_from, window_to = detect._floor_5m(now - timedelta(minutes=30)), now
        got = asyncio.run(detect._fault_history(cached, window_from, window_to))
        got_judged = _summary(_judge(cached, now))
        with _no_cache(monkeypatch):
            fresh = _source(_Shop(orders))
            want = asyncio.run(detect._fault_history(fresh, window_from, window_to))
            want_judged = _summary(_judge(fresh, now))
        assert got == want
        assert got_judged == want_judged
        now += FIVE


def test_reads_per_monitor_run_drop_after_the_first() -> None:
    """첫 감시: 구간 1번 + 지난 7일 날마다 1번 = 8번. 같은 시간 칸 안의 다음 감시: 구간 1번."""
    shop = _Shop(_orders())
    source = _source(shop)
    _judge(source, START)
    assert shop.failed_calls == 8
    shop.failed_calls = 0
    _judge(source, START + FIVE)
    assert shop.failed_calls == 1
    # 1시간 동안(12번) 모두 합쳐도 날마다 새 칸 1번씩만 더 읽는다
    shop.failed_calls = 0
    for k in range(2, 14):
        _judge(source, START + FIVE * k)
    assert shop.failed_calls == 12 + 7


def test_possibly_truncated_read_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """한도만큼 받으면 잘렸을 수 있어 캐시에 넣지 않고, 그 회차는 캐시 없이 날마다 읽는다."""
    monkeypatch.setattr(detect, "FAILED_ORDERS_LIMIT", 3)
    orders = _orders()
    source = _source(_Shop(orders))
    window_from, window_to = START - timedelta(minutes=30), START
    got = asyncio.run(detect._fault_history(source, window_from, window_to))
    assert detect._history_caches[source].slots == {}
    with _no_cache(monkeypatch):
        want = asyncio.run(detect._fault_history(_source(_Shop(orders)), window_from, window_to))
    assert got == want
