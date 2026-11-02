"""campaign_effect skill: 캠페인 효과(주문 증분·쿠폰 누적 사용률).

실제 shop-api 없이 메모리 커넥터로 돈다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from ax_contracts.schemas.data import CampaignEffectInput, CampaignEffectOutput
from data_agent import catalog, detect, handlers
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.shop_api import CampaignCoupon, ShopApi, ShopApiConnector
from data_agent.explain import _Summary
from data_agent.skills.campaign_effect import (
    COUPON_NOTE,
    analyze_campaign,
    measure,
    to_answer,
)

START = datetime(2026, 10, 14, 14, 0, tzinfo=UTC)
END = START + timedelta(hours=2)
STEP = timedelta(minutes=5)


class Memory:
    """테스트 전용 커넥터: 지표는 날마다 같은 시간대 합계를 정해 5분에 고르게 나눈다."""

    def __init__(
        self,
        totals: dict[
            int, float
        ],  # 며칠 전 → 그날 캠페인 시간대 합계 (0 = 캠페인 날). 없는 날은 자료 없음
        coupons: list[CampaignCoupon] | Exception | None = None,
        revenue: dict[int, float] | None = None,
    ) -> None:
        self.totals = totals
        self.revenue = revenue or {}
        self.coupons = coupons
        self.calls: list[str] = []

    async def metric_series(self, metric_id, window_from, window_to, bucket="5m"):  # noqa: ANN001, ANN201
        self.calls.append(metric_id)
        day = round((START - window_from) / timedelta(days=1))
        table = self.totals if metric_id == "order_count" else self.revenue
        if day not in table:
            return []
        slots = int((window_to - window_from) / STEP)
        return [(window_from + STEP * i, table[day] / slots) for i in range(slots)]

    async def failure_events(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        raise NotImplementedError

    async def transactions_volume(self, window_from, window_to):  # noqa: ANN001, ANN201
        return None

    async def campaign_coupons(self, campaign_id: str) -> list[CampaignCoupon]:
        if isinstance(self.coupons, Exception):
            raise self.coupons
        return list(self.coupons or [])


def _week(today: float, usual: float = 100.0) -> dict[int, float]:
    return {0: today, **{d: usual for d in range(1, 8)}}


def _input(campaign_id: str | None = "cmp_1", end: datetime = END) -> CampaignEffectInput:
    return CampaignEffectInput(campaign_id=campaign_id, window_from=START, window_to=end)


def _coupons(*pairs: tuple[int, int]) -> list[CampaignCoupon]:
    return [
        CampaignCoupon(coupon_id=f"cpn_{i}", count=c, used_units=u)
        for i, (c, u) in enumerate(pairs)
    ]


def _measure(memory: Memory, data: CampaignEffectInput | None = None) -> CampaignEffectOutput:
    return asyncio.run(measure(data or _input(), memory))


# ---------------------------------------------------------------- 주문


@pytest.mark.parametrize(
    ("today", "direction"),
    [(200.0, "up"), (103.0, "none"), (97.0, "none"), (50.0, "down")],
    ids=["double", "flat-up", "flat-down", "half"],
)
def test_order_direction_and_excess(today: float, direction: str) -> None:
    orders = _measure(Memory(_week(today))).orders
    assert orders.status == "known" and orders.direction == direction
    assert orders.actual == today and orders.expected == 100.0
    assert orders.excess == today - 100.0  # 증분 = 실제 − 기대 (채점기와 같은 정의)
    assert orders.baseline_days == 7 and orders.ratio == today / 100


def test_expected_is_the_mean_of_days_with_data() -> None:
    totals = {0: 150.0, 1: 90.0, 2: 110.0}  # 3~7일 전은 자료 없음 (0이 아님)
    orders = _measure(Memory(totals)).orders
    assert orders.expected == 100.0 and orders.baseline_days == 2


def test_no_baseline_is_unknown_not_zero() -> None:
    orders = _measure(Memory(_week(40.0, usual=0.0))).orders
    assert orders.status == "unknown" and orders.excess is None
    assert "기준선을 정할 수 없음" in (orders.unknown_reason or "")
    empty = _measure(Memory({})).orders
    # 기록이 하나도 없으면 이상 탐지 빈 구간과 같은 이유 (0건으로 줄었다고 하지 않음)
    assert empty.status == "unknown" and empty.unknown_reason == detect.empty_reason()


def test_campaign_longer_than_a_day_is_unknown() -> None:
    out = _measure(Memory(_week(200.0)), _input(end=START + timedelta(days=2)))
    assert out.orders.status == "unknown" and "하루보다 길어" in (out.orders.unknown_reason or "")
    assert out.revenue.status == "unknown"


# ---------------------------------------------------------------- 매출


def test_revenue_is_unknown_without_a_revenue_metric() -> None:
    memory = Memory(_week(200.0), revenue=_week(2_000_000.0, 1_000_000.0))
    revenue = _measure(memory).revenue
    assert revenue.status == "unknown" and "매출 지표가 없음" in (revenue.unknown_reason or "")
    assert "revenue" not in memory.calls  # 지표가 없으면 부르지 않는다


def test_revenue_uses_the_catalog_line_when_it_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    patched = catalog.current().model_copy(update={"revenue_metric": "revenue"})
    monkeypatch.setattr(catalog, "current", lambda env=None: patched)
    memory = Memory(_week(200.0), revenue=_week(1_500_000.0, 1_000_000.0))
    revenue = _measure(memory).revenue
    assert revenue.status == "known" and revenue.direction == "up"
    assert revenue.excess == 500_000.0 and revenue.p_value is None  # 금액이라 포아송을 쓰지 않음


# ---------------------------------------------------------------- 쿠폰


def test_coupon_rate_is_cumulative_with_the_fixed_note() -> None:
    coupons = _measure(Memory(_week(100.0), coupons=_coupons((3, 3), (3, 1), (3, 0)))).coupons
    assert coupons.status == "known"
    assert (coupons.coupon_count, coupons.issued_units, coupons.used_units) == (3, 9, 4)
    assert coupons.use_rate == round(4 / 9, 4)
    assert (
        coupons.note
        == COUPON_NOTE
        == "조회 시점 기준 누적 사용률이며, 캠페인 뒤 사용분도 섞여 있다"
    )


@pytest.mark.parametrize(
    ("campaign_id", "coupons", "reason"),
    [
        (None, _coupons((3, 1)), "campaign_id가 없어"),
        ("cmp_1", [], "쿠폰이 없음"),
        ("cmp_1", _coupons((0, 0)), "쿠폰이 없음"),
        ("cmp_1", ConnectorUnavailable("UNAVAILABLE", "down"), "쿠폰을 읽지 못함 [UNAVAILABLE]"),
    ],
    ids=["no-campaign-id", "no-coupons", "zero-issued", "read-failed"],
)
def test_coupons_unknown_with_a_reason(campaign_id: str | None, coupons: Any, reason: str) -> None:
    out = _measure(Memory(_week(100.0), coupons=coupons), _input(campaign_id))
    assert out.coupons.status == "unknown" and out.coupons.use_rate is None
    assert reason in (out.coupons.unknown_reason or "")
    assert out.orders.status == "known"  # 쿠폰을 못 봐도 주문은 낸다


def test_connector_without_coupons_is_unknown() -> None:
    class MetricsOnly:  # 다른 회사 커넥터: 지표만 있고 쿠폰 읽기가 없다
        def __init__(self, inner: Memory) -> None:
            self.metric_series = inner.metric_series

    out = asyncio.run(measure(_input(), MetricsOnly(Memory(_week(100.0)))))
    assert "쿠폰을 주지 않음" in (out.coupons.unknown_reason or "")


# ---------------------------------------------------------------- shop-api 커넥터의 쿠폰 읽기


def _shop(handler) -> ShopApiConnector:  # noqa: ANN001
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ShopApiConnector(ShopApi("http://shop.test", token="mock:data", client=client))


def test_shop_api_reads_campaign_coupons() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        rows = [
            {
                "coupon_id": "cpn_1",
                "count": 3,
                "used_units": 2,
                "remaining_units": 1,
                "campaign_id": "cmp_1",
            }
        ]
        return httpx.Response(200, json={"data": rows, "meta": {}})

    coupons = asyncio.run(_shop(handler).campaign_coupons("cmp_1"))
    assert [(c.count, c.used_units) for c in coupons] == [(3, 2)]
    assert seen[0].url.path == "/api/shop/coupons" and seen[0].url.params["campaign_id"] == "cmp_1"


def test_shop_api_bad_coupon_shape_is_bad_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"coupon_id": "cpn_1"}], "meta": {}})

    with pytest.raises(ConnectorUnavailable) as caught:
        asyncio.run(_shop(handler).campaign_coupons("cmp_1"))
    assert caught.value.code == "BAD_RESPONSE"


# ---------------------------------------------------------------- 요약 문장·핸들러


def test_summary_falls_back_when_the_llm_invents_numbers() -> None:
    llm = MockLLMClient(responses={_Summary: _Summary(summary="주문이 999건 늘었습니다.")})
    result = asyncio.run(
        analyze_campaign(_input(), Memory(_week(200.0), coupons=_coupons((3, 2))), llm)
    )
    assert result.fallback and result.fallback_reason == "number_invalid"
    assert "999" not in result.output.summary
    assert result.output.summary.startswith("캠페인 기간 주문 200건으로")
    assert "매출 확인할 수 없음" in result.evidence.data_summary


def _request(payload: dict) -> AgentRequest:
    meta = RequestMeta(
        context_id="ctx_cmp", token="x", deadline=datetime.now(UTC) + timedelta(seconds=30)
    )
    return AgentRequest(meta=meta, skill="campaign_effect", input=payload)


def _handle(monkeypatch: pytest.MonkeyPatch, connector: Any, payload: dict):  # noqa: ANN202
    monkeypatch.setattr(handlers, "get_campaign_connector", lambda: connector)
    monkeypatch.setattr(handlers, "get_llm", lambda: MockLLMClient())
    return asyncio.run(handlers.HANDLERS["campaign_effect"](_request(payload)))


PAYLOAD = {"campaign_id": "cmp_1", "window_from": START.isoformat(), "window_to": END.isoformat()}


def test_handler_completes_with_the_contract_output(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _handle(monkeypatch, Memory(_week(200.0), coupons=_coupons((3, 2))), PAYLOAD)
    assert result.status == TaskStatus.COMPLETED
    out = CampaignEffectOutput.model_validate(result.output)
    assert out.orders.direction == "up" and out.coupons.use_rate == round(2 / 3, 4)
    assert out.revenue.status == "unknown"


def test_handler_reports_unavailable_without_numbers(monkeypatch: pytest.MonkeyPatch) -> None:
    class Down(Memory):
        async def metric_series(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
            raise ConnectorUnavailable("UNAVAILABLE", "shop-api에 연결할 수 없다")

    result = _handle(monkeypatch, Down({}), PAYLOAD)
    assert result.status == TaskStatus.FAILED and result.outcome == "unavailable"
    assert "orders" not in result.output


def test_handler_rejects_a_reversed_window(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = PAYLOAD | {"window_from": END.isoformat(), "window_to": START.isoformat()}
    result = _handle(monkeypatch, Memory(_week(100.0)), bad)
    assert result.status == TaskStatus.FAILED and result.outcome == "invalid_input"


# ---------------------------------------------------------------- 채점기 답 모양


def test_to_answer_matches_the_scorer_shape() -> None:
    out = _measure(Memory(_week(200.0), coupons=_coupons((3, 2))))
    answer = to_answer(out)
    assert answer["orders"] == {"status": "known", "direction": "up", "excess": 100.0}
    assert answer["revenue"]["status"] == "unknown" and answer["revenue"]["unknown_reason"]
    assert answer["coupon"] == {"status": "known", "use_rate": round(2 / 3, 4), "note": COUPON_NOTE}


# ---------------------------------------------------------------- 기록 공백 (줄었음이 아님)


class Slots:
    """5분 칸마다 주문·결제 시도 값을 주는 커넥터.

    `value(시각)`이 0이면 그 칸은 기록 없음처럼 보인다.
    """

    def __init__(self, value) -> None:  # noqa: ANN001
        self.value = value

    def _series(self, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
        out, at = [], start
        while at < end:
            out.append((at, float(self.value(at))))
            at += STEP
        return out

    async def metric_series(self, metric_id, window_from, window_to, bucket="5m"):  # noqa: ANN001, ANN201
        return self._series(window_from, window_to)

    async def transactions_volume(self, window_from, window_to):  # noqa: ANN001, ANN201
        return self._series(window_from, window_to)

    async def campaign_coupons(self, campaign_id: str) -> list[CampaignCoupon]:
        return _coupons((3, 1))


DAY_START = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)  # 한국 12시
DAY_END = DAY_START + timedelta(days=1)


def _day_input() -> CampaignEffectInput:
    return CampaignEffectInput(campaign_id="cmp_1", window_from=DAY_START, window_to=DAY_END)


def _usual(at: datetime) -> int:
    return 2 if 15 <= at.hour < 21 else 5  # UTC 15~21시(한국 새벽)는 적다


def test_server_off_for_the_whole_window_is_unknown_not_a_drop() -> None:
    """서버가 24시간 꺼져 있던 구간: 주문·결제 기록이 하나도 없다.

    → 이상 탐지 빈 구간과 같은 이유.
    """
    connector = Slots(lambda at: 0 if at >= DAY_START else _usual(at))
    out = asyncio.run(measure(_day_input(), connector))
    assert out.orders.status == "unknown" and out.orders.direction is None
    assert out.orders.unknown_reason == detect.empty_reason()
    assert out.orders.unknown_reason.startswith("구간에 주문·결제 기록이 하나도 없음")
    assert out.revenue.status == "unknown"
    assert out.coupons.status == "known"  # 쿠폰은 따로 본다


def test_a_three_hour_gap_is_suspected_not_a_drop() -> None:
    gap_from, gap_to = DAY_START + timedelta(hours=10), DAY_START + timedelta(hours=13)
    connector = Slots(lambda at: 0 if gap_from <= at < gap_to else _usual(at))
    out = asyncio.run(measure(_day_input(), connector))
    assert out.orders.status == "unknown"
    assert (out.orders.unknown_reason or "").startswith("기록 공백 의심 (36칸)")


def test_a_three_hour_gap_at_night_is_also_suspected() -> None:
    """새벽은 원래 0칸이 많아도, 3시간이 통째로 비면 공백이다."""

    def value(at: datetime) -> int:
        night = 15 <= at.hour < 21
        if at >= DAY_START and DAY_START + timedelta(hours=12) <= at < DAY_START + timedelta(
            hours=15
        ):
            return 0
        return (0 if at.minute % 15 == 0 else 1) if night else 5  # 새벽은 3칸 중 1칸이 원래 0

    out = asyncio.run(measure(_day_input(), Slots(value)))
    assert (out.orders.unknown_reason or "").startswith("기록 공백 의심")


def test_a_normal_day_is_judged() -> None:
    out = asyncio.run(measure(_day_input(), Slots(_usual)))
    assert out.orders.status == "known" and out.orders.direction == "none"
    assert out.orders.excess == 0.0


def test_usual_empty_slots_at_night_are_not_a_gap() -> None:
    """새벽에 원래 0칸이 많아도 지난 7일과 비슷하면 공백이 아니다 (평소 0칸 비율과 비교)."""

    def value(at: datetime) -> int:
        return (0 if at.minute % 10 == 0 else 1) if 15 <= at.hour < 21 else 5

    out = asyncio.run(measure(_day_input(), Slots(value)))
    assert out.orders.status == "known"


def test_fully_empty_past_days_are_left_out_of_the_usual_share() -> None:
    """지난날 중 통째로 빈 날(그날도 공백)은 평소 0칸 비율에 넣지 않는다.

    → 오늘 공백을 가리지 않는다.
    """
    gap_from, gap_to = DAY_START + timedelta(hours=10), DAY_START + timedelta(hours=13)
    off_days = {DAY_START.date() - timedelta(days=d) for d in (2, 3)}

    def value(at: datetime) -> int:
        if gap_from <= at < gap_to or (
            at < DAY_START and (at - timedelta(hours=3)).date() in off_days
        ):
            return 0
        return _usual(at)

    out = asyncio.run(measure(_day_input(), Slots(value)))
    assert (out.orders.unknown_reason or "").startswith("기록 공백 의심 (36칸)")


# ---------------------------------------------------------------- 요약: 같음 + 증분 = 추정


@pytest.mark.parametrize(
    ("today", "phrase"),
    [
        (103.0, "증가 추정 +3.0건, 아직 통계적으로 확실하지 않음"),
        (97.0, "감소 추정 -3.0건, 아직 통계적으로 확실하지 않음"),
    ],
)
def test_summary_says_estimate_when_direction_is_flat(today: float, phrase: str) -> None:
    result = asyncio.run(analyze_campaign(_input(), Memory(_week(today)), MockLLMClient()))
    assert result.output.orders.direction == "none"
    assert phrase in result.output.summary
    assert "(같음)" not in result.output.summary


def test_summary_keeps_flat_when_there_is_no_excess() -> None:
    result = asyncio.run(analyze_campaign(_input(), Memory(_week(100.0)), MockLLMClient()))
    assert "+0건(같음)" in result.output.summary or "0건(같음)" in result.output.summary
