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

import math
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from ax_agent_template.llm import LLMClient
from ax_contracts.schemas import Evidence
from ax_contracts.schemas.data import (
    CampaignChange,
    CampaignCoupons,
    CampaignEffectInput,
    CampaignEffectOutput,
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
    return CampaignEffectOutput(
        campaign_id=data.campaign_id,
        window_from=start,
        window_to=end,
        orders=orders,
        revenue=revenue,
        coupons=await _coupons(connector, data.campaign_id),
        summary="",
    )


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
    return facts


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
    notes = [said.summary]
    for name, part in (("주문", output.orders), ("매출", output.revenue), ("쿠폰", output.coupons)):
        if part.status == "unknown":
            notes.append(f"{name} 확인할 수 없음: {part.unknown_reason}")
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
