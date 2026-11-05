"""campaign_effect: 캠페인 효과 (주문·매출 증분, 캠페인 쿠폰 누적 사용률). 읽기 전용(grade read).

계산은 모두 코드다. LLM은 전체 요약 문장만 쓴다(`explain` 숫자 검증 그대로).

- 기준선: 하루 이하 캠페인은 지난 7일 같은 시간대, 하루 넘는 캠페인(7일 이하)은 7일 전·14일 전
  같은 구간(= 시작 전 14일의 같은 요일·시간대). 지난 구간이 빈 구간·기록 공백이면 기준선에서 뺀다.
  요일·날짜 경계는 회사 시간대(`tenant_timezone`, 회사 폴더 `timezone`)로 센다.
- 주문: 캠페인 기간 `order_count` 합계(실제)를 기준선 지난 구간 합계의 평균(기대)과 비교한다.
  증분 = 실제 − 기대 (평가 채점기 `eval/campaign/score.py`와 같은 정의). 방향은 기대를 평균으로 한
  포아송 꼬리확률(`metrics.judge.poisson_upper`·`poisson_lower`)이 `DIRECTION_ALPHA`보다 작을 때만
  늘었음·줄었음이고, 아니면 같음이다. 지표는 커넥터 `metric_series`로 읽는다.
- 매출: 카탈로그 `revenue_metric`이 있으면 같은 방법으로 합계·기대·증분을 낸다
  (방향은 ±10% 임시 기준).
  캣북스는 비어 있다(shop-api metric-series에 매출이 없음) → 확인할 수 없음 + 이유.
- 쿠폰: 커넥터 `campaign_coupons`(shop-api `GET /api/shop/coupons?campaign_id=`)의 발급·사용 장수로
  누적 사용률 = 사용 ÷ 발급. 설명은 고정 문장(`COUPON_NOTE`)이다(LLM이 아님).
- 기록 공백은 "줄었음"이 아니라 unknown + 이유다(shop-api는 주문이 없는 5분을 0으로 준다):
  - 구간에 주문·결제 기록이 하나도 없음: 이상 탐지와 같은 함수(`judge.empty_window`)·문장
    (`detect.empty_reason`)
  - 결제 시도 0인 5분 칸이 `GAP_MIN_RUN`칸 이상 이어지고, 비교 구간에서 가장 길게 이어진 것의
    `GAP_RUN_FACTOR`배 이상: "기록 공백 의심 (N칸)". 통째로 빈 지난 구간은 비교에서 뺀다
- 판정할 수 없으면 0으로 채우지 않고 unknown + 이유: 기준선 주문이 0건, 시작 전 기록 부족(N일),
  기준선에 쓸 지난 구간이 없음, 캠페인이 7일보다 김, campaign_id 없음, 캠페인 쿠폰 없음.
- 주문 지표를 못 읽으면(shop-api 꺼짐) `ConnectorUnavailable`을 그대로 올린다
  (핸들러가 숫자 없이 실패로 답함).

기준값(`DIRECTION_ALPHA`)은 평가 개발 케이스(`eval/campaign/cases/`)로 정했다.
README "campaign_effect".
"""

from __future__ import annotations

import hashlib
import math
import os
import tomllib
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from ax_agent_template.llm import LLMClient
from ax_contracts.schemas import Evidence
from ax_contracts.schemas.data import (
    CampaignChange,
    CampaignCoupons,
    CampaignEffectInput,
    CampaignEffectOutput,
    CampaignFact,
    CampaignMatchedProduct,
    CampaignProductMeasure,
    CampaignProductsEffect,
    CampaignSales,
    CampaignSalesTotals,
)
from data_agent import catalog
from data_agent.connectors import ConnectorUnavailable
from data_agent.detect import empty_reason
from data_agent.explain import explain
from data_agent.metrics.judge import (
    BASELINE_DAYS,
    empty_window,
    poisson_lower,
    poisson_upper,
    to_buckets,
    window_buckets,
)

ORDER_METRIC = "order_count"
DIRECTION_ALPHA = 0.001  # 방향 판정 유의수준 (평가 개발 케이스로 정함, README)
REVENUE_RATIO_BAND = 0.10  # 매출 방향 임시 기준: 기대 대비 ±10% (매출 지표가 생기면 평가로 정한다)
SHORT_WINDOW = timedelta(days=1)  # 이하: 지난 7일 같은 시간대
MAX_WINDOW = timedelta(
    days=7
)  # 이하: 7일 전·14일 전 같은 구간 (넘으면 7일 전 구간이 캠페인과 겹침)
MULTIDAY_WEEKS = (7, 14)  # 같은 요일 = 7일 전, 14일 전
HISTORY_NEEDED_DAYS = 14  # 여러 날 캠페인: 시작 전 이만큼 기록이 있어야 한다
RULE_SHORT = f"지난 {BASELINE_DAYS}일 같은 시간대"
RULE_MULTIDAY = f"시작 전 {HISTORY_NEEDED_DAYS}일의 같은 요일·시간대 (7일 전·14일 전 같은 구간)"
TIMEZONE_ENV = "DATA_TENANT_TIMEZONE"  # 회사 시간대 (scripts/tenant_env.py가 회사 폴더에서 정함)
BACKEND = Path(__file__).resolve().parents[5]
COUPON_NOTE = "조회 시점 기준 누적 사용률이며, 캠페인 뒤 사용분도 섞여 있다"
DIRECTION_KO = {"up": "늘었음", "none": "같음", "down": "줄었음"}
NO_REVENUE = (
    "매출 지표가 없음 (shop-api metric-series에 매출이 없고 카탈로그 revenue_metric이 비어 있음)"
)
# 기록 공백 의심: 결제 시도 0인 5분 칸이 "가장 길게 이어진 길이"가 이만큼 이상이고, 비교 구간의
# 가장 긴 이어짐의 이만큼 배 이상일 때. 서버가 꺼지면 빈 칸이 한 덩어리로 이어지고,
# 주문이 적은 가게의 실제 감소는 빈 칸이 흩어진다.
# 예전 비율 규칙(5%p·12칸)은 둘을 구분하지 못했다(#306 → 이 규칙).
# 근거(README "기록 공백 기준"): 평소 구간은 최장이 비교 구간 최장의 1.81배 이하, 바쁜 가게의
# 3시간 공백은 6배 이상. 1시간보다 짧은 공백은 잡지 않는다
GAP_MIN_RUN = 12  # 1시간
GAP_RUN_FACTOR = 2.0  # 비교 구간 최장 이어짐의 2배
# 확률 규칙(이어짐 규칙과 함께 본다): 그 시간대 평소 5분 결제 시도 수 λ(공백이 아닌 비교 구간)로
# 12칸 이상 이어진 0칸이 우연히 나올 확률 e^(−Σλ)이 이보다 작으면 공백. 비교 구간도 부분적으로
# 꺼져 있어 "평소 최장 이어짐"이 길어진 경우를 잡는다. 근거: README "기록 공백 기준"
GAP_P_MAX = 1e-9


# ---------------------------------------------------------------- 회사 시간대


def tenant_timezone(env: Mapping[str, str] | None = None) -> tzinfo:
    """회사 시간대: `DATA_TENANT_TIMEZONE` → 회사 폴더 `tenant.toml`의 `timezone` → UTC.

    요일·날짜 경계(같은 요일 기준선, 시작 전 기록 일수)를 이 시간대로 센다.
    코드에 시간대를 두지 않는다.
    """
    env = os.environ if env is None else env
    name = env.get(TIMEZONE_ENV)
    if not name:
        path = BACKEND / "config" / "tenants" / (env.get("AX_TENANT") or "catbooks") / "tenant.toml"
        try:
            with path.open("rb") as f:
                name = tomllib.load(f).get("timezone")
        except (OSError, tomllib.TOMLDecodeError):
            name = None
    try:
        return ZoneInfo(name) if isinstance(name, str) and name else UTC
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


def _back(moment: datetime, days: int, tz: tzinfo) -> datetime:
    """회사 시간대의 벽시계로 `days`일 전 같은 시각 (UTC)."""
    return (moment.astimezone(tz) - timedelta(days=days)).astimezone(UTC)


# ---------------------------------------------------------------- 읽은 구간


@dataclass(frozen=True)
class Span:
    """한 구간 [start, end)의 5분 칸별 주문 수·결제 시도 수 (결제 시도를 주지 않으면 None)."""

    label: str  # "캠페인", "3일 전", "7일 전"
    start: datetime
    end: datetime
    orders: dict[datetime, float]
    payments: dict[datetime, float] | None
    has_points: bool  # 주문 지표 점이 하나라도 왔는지 (없으면 자료 없음, 0이 아님)

    def slots(self) -> list[datetime]:
        return window_buckets(self.start, self.end)

    def empty(self) -> bool:
        """주문·결제 기록이 하나도 없음 (이상 탐지 빈 구간과 같은 함수)."""
        return empty_window(self.slots(), self.orders, self.payments or {})

    def zeros(self) -> int | None:
        if self.payments is None:
            return None
        return sum(1 for b in self.slots() if not self.payments.get(b))

    def longest_zero_run(self) -> int | None:
        """결제 시도 0인 5분 칸이 가장 길게 이어진 칸 수. 결제 시도를 주지 않으면 None."""
        if self.payments is None:
            return None
        best = current = 0
        for b in self.slots():
            current = current + 1 if not self.payments.get(b) else 0
            best = max(best, current)
        return best

    def total(self) -> float | None:
        return float(sum(self.orders.values())) if self.has_points else None


def _inside(points: list[Any], start: datetime, end: datetime) -> list[Any]:
    return [(t, v) for t, v in points if start <= t < end and v is not None]


async def _read(connector: Any, label: str, start: datetime, end: datetime) -> Span:
    orders = _inside(await connector.metric_series(ORDER_METRIC, start, end), start, end)
    volume = getattr(connector, "transactions_volume", None)
    payments = await volume(start, end) if volume else None
    return Span(
        label=label,
        start=start,
        end=end,
        orders=to_buckets(orders),
        payments=None if payments is None else to_buckets(_inside(payments, start, end)),
        has_points=bool(orders),
    )


def _cut(whole: Span, label: str, start: datetime, end: datetime) -> Span:
    """이미 읽은 긴 구간에서 [start, end)만 잘라 낸다 (여러 날 캠페인: 14일을 한 번에 읽음)."""
    orders = {t: v for t, v in whole.orders.items() if start <= t < end}
    payments = (
        None
        if whole.payments is None
        else {t: v for t, v in whole.payments.items() if start <= t < end}
    )
    return Span(label, start, end, orders, payments, has_points=bool(orders))


def _hourly_lambda(spans: list[Span]) -> dict[int, float] | None:
    """시간대(UTC 시)별 평소 5분 결제 시도 수 λ. 결제 시도 기록이 있는 구간이 없으면 None.

    비교 구간은 회사 시간대 벽시계로 하루·한 주씩 옮긴 같은 시각이라 시각별로 모을 수 있다.
    """
    usable = [s for s in spans if not s.empty() and s.payments and any(s.payments.values())]
    if not usable:
        return None
    totals: dict[int, float] = {}
    counts: dict[int, int] = {}
    for span in usable:
        for b in span.slots():
            totals[b.hour] = totals.get(b.hour, 0.0) + float((span.payments or {}).get(b) or 0)
            counts[b.hour] = counts.get(b.hour, 0) + 1
    return {h: totals[h] / counts[h] for h in totals}


def _unlikely_run(span: Span, lam: dict[int, float]) -> tuple[int, float] | None:
    """`GAP_MIN_RUN`칸 이상 이어진 0칸 중 평소라면 가장 나오기 어려운 것 (칸 수, 확률 e^(−Σλ))."""
    if span.payments is None:
        return None
    best: tuple[int, float] | None = None
    length, expected = 0, 0.0
    for b in [*span.slots(), None]:  # 끝에 None을 붙여 마지막 이어짐도 닫는다
        if b is not None and not span.payments.get(b):
            length += 1
            expected += lam.get(b.hour, 0.0)
            continue
        if length >= GAP_MIN_RUN and (best is None or expected > best[1]):
            best = (length, expected)
        length, expected = 0, 0.0
    return None if best is None else (best[0], math.exp(-best[1]))


def _chance(p: float) -> str:
    return f"{p:.1e}" if p > 0 else "거의 0"


def _gap(span: Span, others: list[Span], normal: list[Span] | None = None) -> str | None:
    """`span`에 기록 공백이 의심되면 그 이유 (`others` = 비교 구간, 빈 구간은 쓰지 않음).

    `normal`을 주면 평소 λ는 그 구간(공백이 아닌 비교 구간)으로만 구한다. 없으면 `others`.

    둘 중 하나면 공백이다. 흩어진 빈 칸(주문이 적은 가게의 실제 감소)은 잡지 않는다.
    - 이어짐: 가장 길게 이어진 0칸이 `GAP_MIN_RUN` 이상이고
      비교 구간 최장 이어짐의 `GAP_RUN_FACTOR`배 이상
    - 확률: `GAP_MIN_RUN`칸 이상 이어진 0칸이 그 시간대 평소 λ로 우연히 나올 확률이 `GAP_P_MAX` 미만
    """
    run = span.longest_zero_run()
    usable = [o for o in others if not o.empty() and o.longest_zero_run() is not None]
    if run is None or not usable:
        return None
    zeros = span.zeros()
    usual = max(o.longest_zero_run() or 0 for o in usable)
    if run >= GAP_MIN_RUN and run >= GAP_RUN_FACTOR * usual:
        return (
            f"기록 공백 의심 ({zeros}칸): 결제 시도 0인 5분 칸이 {run}칸 이어짐,"
            f" 비교 구간에서 가장 길게 이어진 것은 {usual}칸"
        )
    lam = _hourly_lambda(usable if normal is None else normal)
    unlikely = _unlikely_run(span, lam) if lam else None
    if unlikely and unlikely[1] < GAP_P_MAX:
        return (
            f"기록 공백 의심 ({zeros}칸): 결제 시도 0인 5분 칸이 {unlikely[0]}칸 이어짐,"
            f" 그 시간대 평소 주문으로는 우연히 그럴 확률 {_chance(unlikely[1])}"
        )
    return None


def _baseline(past: list[Span]) -> tuple[list[Span], list[str]]:
    """기준선에 쓸 지난 구간과 뺀 구간의 이유. 빈 구간·기록 공백 의심(나머지와 비교)은 뺀다.

    두 번 본다: 먼저 나머지 지난 구간 전부와 비교해 공백 같은 구간을 찾고, 다시 판정할 때 평소 λ는
    그런 구간을 뺀 나머지(정상 구간)로만 구한다(부분적으로 꺼진 날이 평소 λ를 낮추지 않게).
    이어짐 규칙은 #313 그대로 나머지 지난 구간 전부와 비교한다.
    """
    nonempty = [s for s in past if not s.empty()]
    suspect = {id(s) for s in nonempty if _gap(s, [o for o in nonempty if o is not s])}
    used: list[Span] = []
    dropped: list[str] = []
    for span in past:
        others = [o for o in past if o is not span]
        normal = [o for o in others if not o.empty() and id(o) not in suspect]
        if span.empty():
            dropped.append(f"{span.label}: 주문·결제 기록이 하나도 없음")
        elif why := _gap(span, others, normal or None):
            dropped.append(f"{span.label}: {why}")
        else:
            used.append(span)
    return used, dropped


def _window_problem(window: Span, past: list[Span]) -> str | None:
    """캠페인 구간 기록이 비었거나 공백이 의심되면 그 이유 (#306 규칙)."""
    if window.empty():
        return empty_reason()  # 이상 탐지 빈 구간과 같은 문장
    if why := _gap(window, past):
        return f"{why} (자료가 빠졌을 수 있어 줄었다고 하지 않음)"
    run = window.longest_zero_run()
    if past and run is not None and run >= GAP_MIN_RUN and _hourly_lambda(past) is None:
        return (
            f"기록 공백인지 판단할 수 없음: 결제 시도 0인 5분 칸이 {run}칸 이어졌는데,"
            " 그 시간대 평소 주문 수를 구할 정상 지난 구간(결제 시도 기록)이 없음"
        )
    return None


def _history_days(history: Span, start: datetime, tz: tzinfo) -> int:
    """시작 전 기록이 며칠인지 (회사 시간대 날짜로). 기록이 하나도 없으면 0."""
    seen = [t for t, v in history.orders.items() if v]
    seen += [t for t, v in (history.payments or {}).items() if v]
    if not seen:
        return 0
    return (start.astimezone(tz).date() - min(seen).astimezone(tz).date()).days


def _unknown_change(reason: str, baseline_days: int = 0, rule: str | None = None) -> CampaignChange:
    return CampaignChange(
        status="unknown", unknown_reason=reason, baseline_days=baseline_days, baseline_rule=rule
    )


def _numbers(actual: float, past: list[float]) -> dict[str, Any]:
    expected = sum(past) / len(past)
    return {
        "actual": actual,
        "expected": round(expected, 4),
        "excess": round(actual - expected, 4),
        "ratio": round(actual / expected, 4),
        "baseline_days": len(past),
    }


def judge_orders(
    actual: float | None,
    past: list[float],
    alpha: float = DIRECTION_ALPHA,
    rule: str = RULE_SHORT,
) -> CampaignChange:
    """주문 수 변화. 기대를 평균으로 한 포아송 꼬리확률로 방향을 정한다."""
    if actual is None or not past:
        return _unknown_change(f"캠페인 기간·{rule} 주문 자료가 없음", len(past), rule)
    expected = sum(past) / len(past)
    if expected == 0:
        return _unknown_change(f"{rule} 주문이 0건이라 기준선을 정할 수 없음", len(past), rule)
    observed = int(round(actual))
    p_up = poisson_upper(observed, expected) if observed > expected else 1.0
    p_down = poisson_lower(observed, expected) if observed < expected else 1.0
    direction = "up" if p_up < alpha else "down" if p_down < alpha else "none"
    return CampaignChange(
        status="known",
        direction=direction,
        p_value=min(p_up, p_down, 1.0),
        baseline_rule=rule,
        **_numbers(actual, past),
    )


def judge_revenue(
    actual: float | None, past: list[float], rule: str = RULE_SHORT
) -> CampaignChange:
    """매출 변화. 금액이라 포아송을 쓰지 않고 기대 대비 ±10%로 방향을 정한다(임시)."""
    if actual is None or not past:
        return _unknown_change(f"캠페인 기간·{rule} 매출 자료가 없음", len(past), rule)
    expected = sum(past) / len(past)
    if expected == 0:
        return _unknown_change(f"{rule} 매출이 0이라 기준선을 정할 수 없음", len(past), rule)
    ratio = actual / expected
    if ratio >= 1 + REVENUE_RATIO_BAND:
        direction = "up"
    elif ratio <= 1 - REVENUE_RATIO_BAND:
        direction = "down"
    else:
        direction = "none"
    return CampaignChange(
        status="known", direction=direction, baseline_rule=rule, **_numbers(actual, past)
    )


async def _total(connector: Any, metric: str, start: datetime, end: datetime) -> float | None:
    """[start, end) 지표 합계. 점이 하나도 없으면 None(자료 없음, 0이 아님)."""
    values = [v for _, v in _inside(await connector.metric_series(metric, start, end), start, end)]
    return float(sum(values)) if values else None


async def _coupons(connector: Any, campaign_id: str | None) -> CampaignCoupons:
    if not campaign_id:
        return CampaignCoupons(
            status="unknown", unknown_reason="campaign_id가 없어 캠페인 쿠폰을 찾을 수 없음"
        )
    if not hasattr(connector, "campaign_coupons"):
        return CampaignCoupons(status="unknown", unknown_reason="이 회사 커넥터는 쿠폰을 주지 않음")
    try:
        coupons = await connector.campaign_coupons(campaign_id)
    except ConnectorUnavailable as error:
        return CampaignCoupons(status="unknown", unknown_reason=f"쿠폰을 읽지 못함 [{error.code}]")
    issued = sum(c.count for c in coupons)
    if not coupons or issued == 0:
        return CampaignCoupons(
            status="unknown",
            coupon_count=len(coupons),
            unknown_reason=f"캠페인 {campaign_id} 쿠폰이 없음",
        )
    used = sum(c.used_units for c in coupons)
    return CampaignCoupons(
        status="known",
        coupon_count=len(coupons),
        issued_units=issued,
        used_units=used,
        use_rate=round(min(used / issued, 1.0), 4),
        note=COUPON_NOTE,
    )


async def _spans(
    connector: Any, start: datetime, end: datetime, tz: tzinfo
) -> tuple[Span, list[Span], str, str | None]:
    """(캠페인 구간, 기준선 후보 지난 구간들, 기준선 정의, 판정할 수 없는 이유)."""
    window = await _read(connector, "캠페인", start, end)
    if end - start <= SHORT_WINDOW:
        past = [
            await _read(connector, f"{d}일 전", _back(start, d, tz), _back(end, d, tz))
            for d in range(1, BASELINE_DAYS + 1)
        ]
        return window, past, RULE_SHORT, None
    # 여러 날: 시작 전 15일을 한 번에 읽어 7일 전·14일 전 같은 구간을 잘라 낸다. 하루 더 읽는 건
    # 14일 전 구간이 통째로 비었을 때(서버가 꺼짐) 기록이 짧은 것과 구분하려고다
    history = await _read(connector, "시작 전", _back(start, HISTORY_NEEDED_DAYS + 1, tz), start)
    days = _history_days(history, start, tz)
    if days < HISTORY_NEEDED_DAYS:
        reason = (
            f"시작 전 기록 부족 ({days}일): 같은 요일·시간대 기준선에"
            f" {HISTORY_NEEDED_DAYS}일이 필요함"
        )
        return window, [], RULE_MULTIDAY, reason
    past = [
        _cut(history, f"{k}일 전", _back(start, k, tz), _back(end, k, tz)) for k in MULTIDAY_WEEKS
    ]
    return window, past, RULE_MULTIDAY, None


async def _orders(
    connector: Any, start: datetime, end: datetime, alpha: float, tz: tzinfo
) -> tuple[CampaignChange, list[Span], str]:
    """(주문 변화, 기준선에 쓴 지난 구간, 기준선 정의)."""
    if end - start > MAX_WINDOW:
        reason = (
            "캠페인이 7일보다 길어 7일 전 같은 구간이 캠페인과 겹침"
            " (같은 요일 기준선은 7일 이하 캠페인만)"
        )
        return _unknown_change(reason, rule=RULE_MULTIDAY), [], RULE_MULTIDAY
    window, past, rule, blocked = await _spans(connector, start, end, tz)
    if blocked:
        return _unknown_change(blocked, rule=rule), [], rule
    used, dropped = _baseline(past)
    if problem := _window_problem(window, used):
        return _unknown_change(problem, len(used), rule), used, rule
    if not used:
        why = f"기준선에 쓸 지난 구간이 없음 ({'; '.join(dropped)})"
        return _unknown_change(why, 0, rule), used, rule
    totals = [t for t in (s.total() for s in used) if t is not None]
    orders = judge_orders(window.total(), totals, alpha, rule)
    if dropped and orders.status == "known":
        note = "기준선에서 뺀 구간: " + "; ".join(dropped)
        orders = orders.model_copy(update={"baseline_note": note})
    return orders, used, rule


# ---------------------------------------------------------------- 판매액 (shop-api 주문 기록)

GAP_WORD = "결제 기록 공백"
MIN_BASELINE_ENV = "DATA_CAMPAIGN_SALES_MIN_BASELINE_DAYS"
MIN_BASELINE_DAYS = 3  # 증분 판매액을 내려면 기준선 날이 이만큼 (설정 `MIN_BASELINE_ENV`)


def min_baseline_days(env: Mapping[str, str] | None = None) -> int:
    """증분 판매액에 필요한 기준선 날 수 (설정, 기본 3). 1보다 작거나 숫자가 아니면 기본값."""
    raw = (os.environ if env is None else env).get(MIN_BASELINE_ENV, "")
    try:
        value = int(str(raw).strip())
    except ValueError:
        return MIN_BASELINE_DAYS
    return value if value >= 1 else MIN_BASELINE_DAYS


GAP_HINTS = ("기록 공백", "기록이 하나도 없음", "자료가 빠졌을")  # 주문 판정의 공백 이유 문장


def _totals(orders: list[Any]) -> CampaignSalesTotals:
    """결제 완료 주문의 판매액 합 (원 정수). 성공한 결제가 없는 주문은 세지 않는다."""
    done = [o for o in orders if o.settled]
    return CampaignSalesTotals(
        orders=len(done),
        gross_krw=sum(o.gross for o in done),
        discount_krw=sum(o.coupon_discount for o in done),
        paid_krw=sum(o.paid for o in done),
        refund_krw=sum(o.refunded_amount for o in done),
    )


def _mean_totals(days: list[CampaignSalesTotals]) -> CampaignSalesTotals:
    n = len(days)
    return CampaignSalesTotals(
        orders=round(sum(d.orders for d in days) / n),
        gross_krw=round(sum(d.gross_krw for d in days) / n),
        discount_krw=round(sum(d.discount_krw for d in days) / n),
        paid_krw=round(sum(d.paid_krw for d in days) / n),
        refund_krw=round(sum(d.refund_krw for d in days) / n),
    )


def _unknown_sales(reason: str, rule: str | None = None, days: int = 0) -> CampaignSales:
    gap = any(h in reason for h in GAP_HINTS) and not reason.startswith(GAP_WORD)
    return CampaignSales(
        status="unknown", unknown_reason=f"{GAP_WORD}: {reason}" if gap else reason,
        baseline_rule=rule, baseline_days=days,
    )  # fmt: skip


def _span_text(start: datetime, end: datetime) -> str:
    return f"{start.astimezone(UTC):%Y-%m-%dT%H:%MZ}~{end.astimezone(UTC):%Y-%m-%dT%H:%MZ}"


async def _sales(
    connector: Any,
    data: CampaignEffectInput,
    orders: CampaignChange,
    used: list[Span],
    rule: str,
) -> CampaignSales:
    """판매액 (증분). 기준선은 주문 수 판정이 쓴 지난 구간 그대로 (빈 구간·공백은 이미 빠짐)."""
    if not hasattr(connector, "orders"):
        return _unknown_sales("이 회사 커넥터는 주문 금액을 주지 않음", rule)
    if orders.status == "unknown":  # 기록 공백·기준선 없음 등은 주문과 같은 이유 (공백 규칙 재사용)
        return _unknown_sales(orders.unknown_reason or "주문을 판정할 수 없음", rule,
                              orders.baseline_days)  # fmt: skip
    start, end = data.window_from, data.window_to
    try:
        window = await connector.orders(start, end)
        past = [await connector.orders(s.start, s.end) for s in used]
    except ConnectorUnavailable as error:
        return _unknown_sales(f"주문 금액을 읽지 못함 [{error.code}]", rule, len(used))
    period = _totals(window)
    unpaid = sum(1 for o in window if not o.settled and o.status == "pending_payment")
    if period.orders == 0 and unpaid:
        return _unknown_sales(
            f"{GAP_WORD}: 구간 주문 {unpaid}건이 결제 대기이고 결제 완료가 0건", rule, len(used)
        )
    days = [_totals(p) for p in past]
    need = min_baseline_days()
    if len(days) < need:  # 증분·범위·방향은 모름, 기간 판매액·쿠폰 귀속(기간 숫자)은 준다
        direct, direct_orders, direct_why = await _direct(connector, data.campaign_id, window)
        return CampaignSales(
            status="unknown", unknown_reason=f"기준선 부족({len(days)}일, 최소 {need}일)",
            period=period, baseline=_mean_totals(days) if days else None, baseline_rule=rule,
            baseline_days=len(days), baseline_note=orders.baseline_note,
            orders_direction=orders.direction, direct_attributed_krw=direct,
            direct_orders=direct_orders, direct_reason=direct_why,
        )  # fmt: skip
    base = _mean_totals(days)
    excess = period.gross_krw - base.gross_krw
    low, high, direction, why = _range(period.gross_krw, [d.gross_krw for d in days], "판매액이")
    why += f" (주문 수 판정: {DIRECTION_KO[orders.direction or 'none']})"
    direct, direct_orders, direct_why = await _direct(connector, data.campaign_id, window)
    return CampaignSales(
        status="known", period=period, baseline=base, baseline_rule=rule,
        baseline_days=len(days), baseline_note=orders.baseline_note,
        incremental_gross_krw=excess, incremental_paid_krw=period.paid_krw - base.paid_krw,
        incremental_low_krw=low, incremental_high_krw=high, direction=direction,
        direction_reason=why, orders_direction=orders.direction,
        direct_attributed_krw=direct, direct_orders=direct_orders, direct_reason=direct_why,
    )  # fmt: skip


def _range(
    period: float, days: list[float], what: str
) -> tuple[float, float, Literal["up", "down", "uncertain"], str]:
    """기준선 날들의 범위로 낸 증분 하한·상한과 방향·이유 (판매액·상품 기준이 같이 쓴다).

    하한 = 구간 − 가장 많은 날, 상한 = 구간 − 가장 적은 날. 하한이 0보다 크면 up, 상한이 0보다
    작으면 down, 걸치거나 기준선 날이 1개면 uncertain.
    """
    low, high = period - max(days), period - min(days)
    n = len(days)
    if n < 2:
        return low, high, "uncertain", f"기준선 날이 {n}개라 범위를 낼 수 없음"
    if low > 0:
        return low, high, "up", f"구간 {what} 기준선 {n}일 중 가장 많은 날보다 많음"
    if high < 0:
        return low, high, "down", f"구간 {what} 기준선 {n}일 중 가장 적은 날보다 적음"
    return low, high, "uncertain", f"구간 {what} 기준선 {n}일 범위 안이라 확실하지 않음"


async def _direct(
    connector: Any, campaign_id: str | None, window: list[Any]
) -> tuple[int | None, int | None, str | None]:
    """쿠폰 귀속: 캠페인 쿠폰을 쓴 결제 완료 구간 주문의 판매액 합."""
    if not campaign_id:
        return None, None, "campaign_id가 없어 쿠폰 귀속을 볼 수 없음"
    if not hasattr(connector, "campaign_coupons"):
        return None, None, "이 회사 커넥터는 쿠폰을 주지 않음"
    try:
        coupons = {c.coupon_id for c in await connector.campaign_coupons(campaign_id)}
    except ConnectorUnavailable as error:
        return None, None, f"쿠폰을 읽지 못함 [{error.code}]"
    if not coupons:
        return None, None, f"캠페인 {campaign_id} 쿠폰이 없음"
    hits = [o for o in window if o.settled and o.coupon_id in coupons]
    return sum(o.gross for o in hits), len(hits), None


# ---------------------------------------------------------------- 상품 기준 (#646)

NO_MATCH = "맞는 상품 없음"
PRODUCT_PARTS = (("orders", "주문 수가"), ("units", "판매 수량이"), ("gross_krw", "판매액이"))


def normalize_name(text: str) -> str:
    """상품 이름 비교용: NFKC, 소문자, 공백 모두 뺌. 이 값이 정확히 같을 때만 같은 상품이다."""
    return "".join(unicodedata.normalize("NFKC", text).lower().split())


def match_products(
    asked: list[str], catalog: list[Any]
) -> tuple[list[CampaignMatchedProduct], list[str]]:
    """요청 값 → (맞은 상품, 못 맞춘 값). id가 정확히 같거나 정규화한 이름이 정확히 같을 때만.

    일부만 같은 이름은 맞추지 않는다(추측하지 않음). 같은 상품을 두 번 요청하면 한 번만 센다.
    """
    by_id = {p.product_id: p for p in catalog}
    by_name = {normalize_name(p.name): p for p in catalog}
    matched: list[CampaignMatchedProduct] = []
    unmatched: list[str] = []
    for value in asked:
        product = by_id.get(value.strip()) or by_name.get(normalize_name(value))
        if product is None:
            unmatched.append(value)
        elif all(m.product_id != product.product_id for m in matched):
            matched.append(CampaignMatchedProduct(product_id=product.product_id,
                                                  name=product.name, asked=value))  # fmt: skip
    return matched, unmatched


def _product_tally(orders: list[Any], ids: set[str]) -> dict[str, float]:
    """결제 완료 주문 중 맞은 상품의 품목만: 그 상품이 든 주문 수, 수량 합, 단가 × 수량 합."""
    out = {"orders": 0.0, "units": 0.0, "gross_krw": 0.0}
    for order in orders:
        lines = [i for i in order.items if i.product_id in ids] if order.settled else []
        if lines:
            out["orders"] += 1
            out["units"] += sum(i.qty for i in lines)
            out["gross_krw"] += sum(i.qty * i.unit_price for i in lines)
    return out


async def _products_effect(
    connector: Any,
    data: CampaignEffectInput,
    orders: CampaignChange,
    used: list[Span],
    rule: str,
) -> CampaignProductsEffect | None:
    """관련 상품 기준. 기준선·공백 규칙은 판매액과 같다 (주문 수 판정이 쓴 지난 구간 그대로)."""
    if not data.products:
        return None

    def unknown(
        reason: str, matched: list[CampaignMatchedProduct], unmatched: list[str], **more: Any
    ) -> CampaignProductsEffect:
        gap = any(h in reason for h in GAP_HINTS) and not reason.startswith(GAP_WORD)
        return CampaignProductsEffect(
            status="unknown", unknown_reason=f"{GAP_WORD}: {reason}" if gap else reason,
            matched=matched, unmatched_products=unmatched, baseline_rule=rule, **more,
        )  # fmt: skip

    if not hasattr(connector, "products") or not hasattr(connector, "orders"):
        return unknown("이 회사 커넥터는 상품·주문 기록을 주지 않음", [], list(data.products))
    try:
        matched, unmatched = match_products(list(data.products), await connector.products())
    except ConnectorUnavailable as error:
        return unknown(f"상품 목록을 읽지 못함 [{error.code}]", [], list(data.products))
    if not matched:
        return unknown(f"{NO_MATCH} (요청 {len(unmatched)}개 모두 카탈로그 이름·id와 다름)",
                       [], unmatched)  # fmt: skip
    if orders.status == "unknown":
        return unknown(orders.unknown_reason or "주문을 판정할 수 없음", matched, unmatched,
                       baseline_days=orders.baseline_days)  # fmt: skip
    try:
        window = await connector.orders(data.window_from, data.window_to)
        past = [await connector.orders(s.start, s.end) for s in used]
    except ConnectorUnavailable as error:
        return unknown(f"주문 기록을 읽지 못함 [{error.code}]", matched, unmatched)
    unpaid = sum(1 for o in window if not o.settled and o.status == "pending_payment")
    if not any(o.settled for o in window) and unpaid:
        return unknown(f"{GAP_WORD}: 구간 주문 {unpaid}건이 결제 대기이고 결제 완료가 0건",
                       matched, unmatched, baseline_days=len(used))  # fmt: skip
    ids = {m.product_id for m in matched}
    now = _product_tally(window, ids)
    days = [_product_tally(p, ids) for p in past]
    need = min_baseline_days()
    common: dict[str, Any] = dict(
        matched=matched, unmatched_products=unmatched, baseline_rule=rule,
        baseline_days=len(days), baseline_note=orders.baseline_note,
    )  # fmt: skip
    if len(days) < need:  # 증분·범위·방향은 모름, 기간값만
        parts = {k: CampaignProductMeasure(period=now[k]) for k, _ in PRODUCT_PARTS}
        return CampaignProductsEffect(
            status="unknown", unknown_reason=f"기준선 부족({len(days)}일, 최소 {need}일)",
            **parts, **common,
        )  # fmt: skip
    measures: dict[str, CampaignProductMeasure] = {}
    for key, what in PRODUCT_PARTS:
        values = [d[key] for d in days]
        base = sum(values) / len(values)
        low, high, direction, why = _range(now[key], values, what)
        measures[key] = CampaignProductMeasure(
            period=now[key], baseline=round(base, 4), incremental=round(now[key] - base, 4),
            incremental_low=low, incremental_high=high, direction=direction, direction_reason=why,
        )  # fmt: skip
    return CampaignProductsEffect(status="known", **measures, **common)


def product_facts(out: CampaignEffectOutput) -> list[CampaignFact]:
    """상품 기준 근거 숫자 (출처·기간·표본). 있는 숫자만 (모르는 칸은 0으로 채우지 않음)."""
    p = out.products_effect
    if p is None:
        return []
    span = _span_text(out.window_from, out.window_to)
    names = ", ".join(m.name or m.product_id for m in p.matched)
    rows: list[CampaignFact] = []
    units = {"orders": "건", "units": "개", "gross_krw": "KRW"}
    labels = {"orders": "주문 수", "units": "판매 수량", "gross_krw": "판매액"}
    for key, _ in PRODUCT_PARTS:
        m: CampaignProductMeasure | None = getattr(p, key)
        if m is None:
            continue
        for label, value in (
            ("구간", m.period),
            ("기준선 (날 평균)", m.baseline),
            ("증분", m.incremental),
            ("증분 하한", m.incremental_low),
            ("증분 상한", m.incremental_high),
        ):
            if value is not None:
                rows.append(CampaignFact(
                    label=f"상품 기준 {labels[key]} {label} ({names})", value=value,
                    unit=units[key], period=span, sample=p.baseline_days,
                ))  # fmt: skip
    return rows


def sales_facts(out: CampaignEffectOutput) -> list[CampaignFact]:
    """판매액 근거 숫자 (출처·기간·표본). 있는 숫자만 넣는다 (모르는 칸은 0으로 채우지 않음).

    기준선이 부족해 증분을 모를 때도 구간 판매액은 넣는다.
    """
    s = out.sales
    if s is None or s.period is None:
        return []
    span = _span_text(out.window_from, out.window_to)
    p, b = s.period, s.baseline

    def fact(label: str, value: float, sample: int | None, unit: str = "KRW") -> CampaignFact:
        return CampaignFact(label=label, value=value, unit=unit, period=span, sample=sample)

    rows = [
        fact("구간 주문 금액 합 (할인 전)", p.gross_krw, p.orders),
        fact("구간 쿠폰 할인 합", p.discount_krw, p.orders),
        fact("구간 결제 금액 합", p.paid_krw, p.orders),
        fact("구간 환불 합", p.refund_krw, p.orders),
        fact("구간 결제 완료 주문", p.orders, p.orders, unit="건"),
    ]
    if b is not None:
        rows.append(fact("기준선 주문 금액 (날 평균)", b.gross_krw, s.baseline_days))
    for label, value in (
        ("증분 판매액 (주문 금액)", s.incremental_gross_krw),
        ("증분 판매액 하한", s.incremental_low_krw),
        ("증분 판매액 상한", s.incremental_high_krw),
    ):
        if value is not None:
            rows.append(fact(label, value, s.baseline_days))
    if s.direct_attributed_krw is not None:
        rows.append(fact("쿠폰 귀속 판매액", s.direct_attributed_krw, s.direct_orders))
    return rows


async def measure(
    data: CampaignEffectInput,
    connector: Any,
    *,
    alpha: float = DIRECTION_ALPHA,
    env: Mapping[str, str] | None = None,
) -> CampaignEffectOutput:
    """설명 문장 없이 숫자만 계산한다 (평가 채점기도 이 함수를 부른다)."""
    start, end = data.window_from, data.window_to
    orders, used, rule = await _orders(connector, start, end, alpha, tenant_timezone(env))
    revenue_metric = catalog.current().revenue_metric
    if not revenue_metric:
        revenue = _unknown_change(NO_REVENUE)
    elif orders.status == "unknown":
        revenue = _unknown_change(orders.unknown_reason or "", orders.baseline_days, rule)
    else:
        actual = await _total(connector, revenue_metric, start, end)
        totals = [await _total(connector, revenue_metric, s.start, s.end) for s in used]
        revenue = judge_revenue(actual, [t for t in totals if t is not None], rule)
    out = CampaignEffectOutput(
        campaign_id=data.campaign_id,
        window_from=start,
        window_to=end,
        orders=orders,
        revenue=revenue,
        coupons=await _coupons(connector, data.campaign_id),
        summary="",
        # 기획이 결과를 기록할 때 잇는 id: 요청 값 그대로 돌려준다 (데이터는 저장하지 않는다)
        insight_id=data.insight_id,
        proposal_id=data.proposal_id,
        sales=await _sales(connector, data, orders, used, rule),
        products_effect=await _products_effect(connector, data, orders, used, rule),
    )
    if orders.status == "known" and orders.excess is not None:
        out.incremental_orders = round(orders.excess)  # 재무 orders 칸 (증분, gmv_krw와 같은 뜻)
    out.schema_version = SCHEMA_VERSION
    out.generated_at = datetime.now(UTC)
    out.facts = campaign_facts(out)
    return out


# 응답 모양 버전 (v2: sales·facts·incremental_orders, v3: products_effect #646)
SCHEMA_VERSION = "campaign_effect.v3"


def _fact_id(label: str) -> str:
    return "cmp_" + hashlib.sha256(label.encode("utf-8")).hexdigest()[:10]


def campaign_facts(out: CampaignEffectOutput) -> list[CampaignFact]:
    """응답에 나오는 숫자 전부 (주문·쿠폰·판매액). 요약 문장 재료(`_facts`)의 숫자도 모두 여기 있다.

    모르는 칸은 넣지 않는다 (0으로 채우지 않음). fact_id는 같은 칸이면 같은 값이다.
    """
    span = _span_text(out.window_from, out.window_to)
    rows: list[CampaignFact] = []

    def add(label: str, value: float | None, unit: str, sample: int | None = None) -> None:
        if value is not None:
            rows.append(CampaignFact(label=label, value=value, unit=unit, period=span,
                                     sample=sample, fact_id=_fact_id(label)))  # fmt: skip

    o = out.orders
    if o.status == "known":
        days = o.baseline_days
        add("캠페인 주문", int(o.actual) if o.actual is not None else None, "건", days)
        add("평소 주문", round(o.expected, 1) if o.expected is not None else None, "건", days)
        add("주문 증분", round(o.excess, 1) if o.excess is not None else None, "건", days)
        add("증분 주문 수", out.incremental_orders, "건", days)
        add("실제 ÷ 기대", o.ratio, "배", days)
        add("주문 방향 꼬리확률", o.p_value, "확률", days)
        add("기준 일수", days, "구간", days)
    c = out.coupons
    if c.status == "known":
        add("쿠폰 발급", c.issued_units, "장", c.coupon_count)
        add("쿠폰 사용", c.used_units, "장", c.coupon_count)
        add("쿠폰 사용률", c.use_rate, "비율", c.coupon_count)
    for f in sales_facts(out):
        rows.append(f.model_copy(update={"fact_id": _fact_id(f.label)}))
    s = out.sales
    if s is not None and s.status == "known" and s.incremental_gross_krw is not None:
        add("증분 판매액(원)", s.incremental_gross_krw, "KRW", s.baseline_days)  # 요약 재료 이름
    for f in product_facts(out):
        rows.append(f.model_copy(update={"fact_id": _fact_id(f.label)}))
    p = out.products_effect
    gross = p.gross_krw if p is not None and p.status == "known" else None
    if p is not None and gross is not None and gross.incremental is not None:
        add("상품 기준 증분 판매액(원)", round(gross.incremental), "KRW", p.baseline_days)
    return rows


# ---------------------------------------------------------------- 설명 (LLM은 요약 문장만)


def _facts(out: CampaignEffectOutput) -> dict[str, int | float | str]:
    facts: dict[str, int | float | str] = {}
    o = out.orders
    if o.status == "known":
        direction = DIRECTION_KO[o.direction or "none"]
        if o.direction == "none" and o.excess:  # 화면(#294)과 같은 규칙
            direction = f"{'증가' if o.excess > 0 else '감소'} 추정, 아직 통계적으로 확실하지 않음"
        facts |= {
            "주문 방향": direction,
            "캠페인 주문": int(o.actual or 0),
            "평소 주문": round(o.expected or 0, 1),
            "주문 증분": round(o.excess or 0, 1),
            "기준 일수": o.baseline_days,
        }
    else:
        facts["주문"] = f"확인할 수 없음: {o.unknown_reason}"
    c = out.coupons
    if c.status == "known":
        facts |= {"쿠폰 사용률": c.use_rate or 0.0, "쿠폰 발급": c.issued_units or 0,
                  "쿠폰 사용": c.used_units or 0}  # fmt: skip
    else:
        facts["쿠폰"] = f"확인할 수 없음: {c.unknown_reason}"
    if out.revenue.status == "unknown":
        facts["매출"] = "확인할 수 없음"
    s = out.sales
    if s is not None and s.status == "known" and s.incremental_gross_krw is not None:
        facts["증분 판매액(원)"] = s.incremental_gross_krw
        facts["판매액 방향"] = SALES_DIRECTION_KO[s.direction or "uncertain"]
    elif s is not None:
        facts["판매액"] = f"확인할 수 없음: {s.unknown_reason}"
    p = out.products_effect
    gross = p.gross_krw if p is not None and p.status == "known" else None
    if gross is not None and gross.incremental is not None:
        facts["상품 기준 증분 판매액(원)"] = round(gross.incremental)
        facts["상품 기준 판매액 방향"] = SALES_DIRECTION_KO[gross.direction or "uncertain"]
    elif p is not None:
        facts["상품 기준"] = f"확인할 수 없음: {p.unknown_reason}"
    return facts


SALES_DIRECTION_KO = {"up": "늘었음", "down": "줄었음", "uncertain": "확실하지 않음"}


@dataclass(frozen=True)
class CampaignEffectResult:
    output: CampaignEffectOutput
    evidence: Evidence
    fallback: bool
    summary: str | None
    fallback_reason: str | None = None
    fallback_numbers: tuple[float, ...] = ()


async def analyze_campaign(
    data: CampaignEffectInput, connector: Any, llm: LLMClient
) -> CampaignEffectResult:
    measured = await measure(data, connector)
    said = await explain("campaign", _facts(measured), llm)
    output = measured.model_copy(update={"summary": said.summary})
    span = f"{data.window_from.isoformat()}~{data.window_to.isoformat()}"
    queries = [
        f"shop_metric_series metric={ORDER_METRIC} {span} + 지난 {BASELINE_DAYS}일 같은 시간대"
    ]
    if data.campaign_id:
        queries.append(f"shop_coupons campaign_id={data.campaign_id}")
    if data.products:
        queries.append(f"shop_products + shop_orders {span} 관련 상품 {len(data.products)}개")
    notes = [said.summary]
    for name, part in (("주문", output.orders), ("매출", output.revenue), ("쿠폰", output.coupons)):
        if part.status == "unknown":
            notes.append(f"{name} 확인할 수 없음: {part.unknown_reason}")
    p = output.products_effect
    if p is not None and p.status == "unknown":
        notes.append(f"상품 기준 확인할 수 없음: {p.unknown_reason}")
    if p is not None and p.unmatched_products:
        notes.append(f"못 맞춘 상품: {', '.join(p.unmatched_products)}")
    if note := said.note():
        notes.append(note)
    return CampaignEffectResult(
        output=output,
        evidence=Evidence(
            queries=queries, data_summary="\n".join(notes), collected_at=datetime.now(UTC)
        ),
        fallback=said.fallback,
        summary=said.summary,
        fallback_reason=said.reason,
        fallback_numbers=said.bad_numbers,
    )


# ---------------------------------------------------------------- 평가 채점기용 (campaign/score.py)


def to_answer(out: CampaignEffectOutput) -> dict[str, Any]:
    """skill 출력 → 채점기 답 모양(`score.Answer`)."""

    def change(c: CampaignChange) -> dict[str, Any]:
        if c.status == "unknown":
            return {"status": "unknown", "unknown_reason": c.unknown_reason}
        return {"status": "known", "direction": c.direction, "excess": c.excess}

    coupons = out.coupons
    coupon = (
        {"status": "unknown", "unknown_reason": coupons.unknown_reason}
        if coupons.status == "unknown"
        else {"status": "known", "use_rate": coupons.use_rate, "note": coupons.note}
    )
    return {"orders": change(out.orders), "revenue": change(out.revenue), "coupon": coupon}


async def eval_judge(
    request: Mapping[str, Any], transport: httpx.MockTransport, *, alpha: float = DIRECTION_ALPHA
) -> dict[str, Any]:
    """채점기가 부르는 판정 함수: 케이스 데이터로 만든 shop-api를 실제 커넥터로 읽는다."""
    from data_agent.connectors.shop_api import ShopApi, ShopApiConnector

    async with httpx.AsyncClient(transport=transport) as client:
        connector = ShopApiConnector(ShopApi("http://shop.eval", token="eval", client=client))
        out = await measure(
            CampaignEffectInput.model_validate(dict(request)), connector, alpha=alpha
        )
    return to_answer(out)


def to_sales_answer(out: CampaignEffectOutput) -> dict[str, Any]:
    """skill 출력 → 매출 채점기 답 모양(`campaign_revenue/score.Answer`)."""
    s = out.sales
    if s is None or s.status == "unknown":
        reason = s.unknown_reason if s is not None else "판매액 칸이 없음"
        return {"sales": {"status": "unknown", "unknown_reason": reason}}
    return {"sales": {
        "status": "known", "direction": s.direction,
        "incremental_gross_krw": s.incremental_gross_krw,
        "direct_attributed_krw": s.direct_attributed_krw,
    }}  # fmt: skip


def to_products_answer(out: CampaignEffectOutput) -> dict[str, Any]:
    """skill 출력 → 상품 기준 채점기 답 모양(`campaign_products/score.Answer`)."""
    p = out.products_effect
    if p is None:
        return {"products": {"status": "unknown", "unknown_reason": "상품 기준 칸이 없음"}}
    body: dict[str, Any] = {
        "status": p.status, "unknown_reason": p.unknown_reason,
        "matched": [m.product_id for m in p.matched], "unmatched": p.unmatched_products,
    }  # fmt: skip
    if p.status == "known":
        for key, name in (("orders", "orders"), ("units", "units"), ("gross_krw", "gross")):
            m = getattr(p, key)
            body[name] = {"direction": m.direction, "incremental": m.incremental}
    return {"products": body}


async def eval_products_judge(
    request: Mapping[str, Any], transport: httpx.MockTransport, *, alpha: float = DIRECTION_ALPHA
) -> dict[str, Any]:
    """상품 기준 채점기(`eval/campaign_products/score.py`)가 부르는 판정 함수."""
    from data_agent.connectors.shop_api import ShopApi, ShopApiConnector

    async with httpx.AsyncClient(transport=transport) as client:
        connector = ShopApiConnector(ShopApi("http://shop.eval", token="eval", client=client))
        out = await measure(
            CampaignEffectInput.model_validate(dict(request)), connector, alpha=alpha
        )
    return to_products_answer(out)


async def eval_sales_judge(
    request: Mapping[str, Any], transport: httpx.MockTransport, *, alpha: float = DIRECTION_ALPHA
) -> dict[str, Any]:
    """매출 채점기(`eval/campaign_revenue/score.py`)가 부르는 판정 함수."""
    from data_agent.connectors.shop_api import ShopApi, ShopApiConnector

    async with httpx.AsyncClient(transport=transport) as client:
        connector = ShopApiConnector(ShopApi("http://shop.eval", token="eval", client=client))
        out = await measure(
            CampaignEffectInput.model_validate(dict(request)), connector, alpha=alpha
        )
    return to_sales_answer(out)
