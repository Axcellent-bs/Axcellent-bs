"""이상 탐지 판정 (anomaly_detection skill). 순수 계산이며 LLM을 쓰지 않는다.

정확도 평가(PR #55)에서 나온 문제를 고친 판정이다.
- 비율 지표(결제 실패율)와 건수 지표(주문 수·실패 주문 수·문의 수)를 나눠 판정한다.
  - 비율: 5분 결제 건수가 `min_payments` 이상인 구간만 판정한다. 지난 7일 같은 시간대의
    실패 비율을 기준으로, 실패 건수가 이항분포로 우연히 나올 확률(p값)이 작으면 이상이다(상승만).
  - 건수: 지난 7일 같은 시간대 평균을 기준으로 포아송 p값을 본다.
    - 주문 수는 **감소**만 이상이다. 증가(세일·캠페인)만으로는 이상이 아니다.
    - 실패 주문 수는 증가가 이상이다. 단, 주문이 평소보다 많으면 그만큼 기준을 늘려 본다
      (주문이 2배면 실패도 2배까지는 정상).
    - 문의 수는 증가가 이상이다.
  - 구간 합계 검정(유의수준 `alpha`)과 5분 구간별 검정(`alpha`를 구간 수로 나눔)을 함께 본다.
- 판정할 수 없으면 "이상 없음"이 아니라 "확인할 수 없음"(`unknown`)이다.
  - 기준선이 0 (지난 7일 같은 시간대 값이 모두 0, 실패 0건)
  - 결제 건수가 모자라 실패율을 판정할 5분 구간이 없음
  - 구간에 주문·결제 기록이 하나도 없음 (빈 구간)
  - 지표 자료가 없음
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

Status = Literal["normal", "anomaly", "unknown"]
Direction = Literal["up", "down"]
Point = tuple[datetime, float | None]

BASELINE_DAYS = 7
BUCKET = timedelta(minutes=5)
# 기본값은 정확도 평가(domains/data/eval)로 정했다. 회사별로 바꿀 수 있다(`settings_for`)
DEFAULT_MIN_PAYMENTS = 5
DEFAULT_ALPHA = 0.001

RATE_METRICS = frozenset({"payment_fail_rate"})
DROP_METRICS = frozenset({"order_count"})  # 감소만 이상
RISE_METRICS = frozenset({"order_fail_count", "inquiry_count"})  # 증가만 이상


@dataclass(frozen=True)
class Settings:
    min_payments: int = DEFAULT_MIN_PAYMENTS
    alpha: float = DEFAULT_ALPHA


def settings_for(tenant_id: str | None, env: Mapping[str, str]) -> Settings:
    """회사별 판정 설정.

    - `DATA_ANOMALY_MIN_PAYMENTS`·`DATA_ANOMALY_ALPHA`: 모든 회사 기본값
    - `DATA_ANOMALY_SETTINGS`: 회사별 덮어쓰기 (`tenant=min_payments:alpha;…`,
      예 `shop-demo=8:0.001;big-shop=20:0.0005`). 형식이 틀린 항목은 무시한다.
    """
    base = Settings(
        min_payments=_int(env.get("DATA_ANOMALY_MIN_PAYMENTS"), DEFAULT_MIN_PAYMENTS),
        alpha=_float(env.get("DATA_ANOMALY_ALPHA"), DEFAULT_ALPHA),
    )
    for item in (env.get("DATA_ANOMALY_SETTINGS") or "").split(";"):
        name, _, values = item.partition("=")
        if tenant_id and name.strip() == tenant_id:
            payments, _, alpha = values.partition(":")
            return Settings(
                min_payments=_int(payments, base.min_payments), alpha=_float(alpha, base.alpha)
            )
    return base


def _int(text: str | None, default: int) -> int:
    try:
        value = int(str(text).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _float(text: str | None, default: float) -> float:
    try:
        value = float(str(text).strip())
    except (TypeError, ValueError):
        return default
    return value if 0 < value < 1 else default


# ---------------------------------------------------------------- 확률


def _logsumexp(values: list[float]) -> float:
    top = max(values)
    return top + math.log(sum(math.exp(v - top) for v in values))


def _poisson_logpmf(k: int, lam: float) -> float:
    return -lam + k * math.log(lam) - math.lgamma(k + 1)


def poisson_upper(k: int, lam: float) -> float:
    """P(X ≥ k), X ~ Poisson(lam)."""
    if k <= 0:
        return 1.0
    below = _logsumexp([_poisson_logpmf(i, lam) for i in range(k)])
    return max(0.0, -math.expm1(below))


def poisson_lower(k: int, lam: float) -> float:
    """P(X ≤ k), X ~ Poisson(lam)."""
    if k < 0:
        return 0.0
    return min(1.0, math.exp(_logsumexp([_poisson_logpmf(i, lam) for i in range(k + 1)])))


def binom_upper(k: int, n: int, p: float) -> float:
    """P(X ≥ k), X ~ Binomial(n, p)."""
    if k <= 0:
        return 1.0
    if p >= 1:
        return 1.0
    terms = [
        math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
        + i * math.log(p) + (n - i) * math.log1p(-p)
        for i in range(k, n + 1)
    ]  # fmt: skip
    return min(1.0, math.exp(_logsumexp(terms))) if terms else 0.0


# ---------------------------------------------------------------- 판정


@dataclass(frozen=True)
class Judgement:
    metric: str
    status: Status
    observed: float  # 비율: 판정한 구간의 실패율, 건수: 구간 합계
    baseline: float  # 비율: 기준 실패율, 건수: 기준 합계(지난 7일 같은 시간대 평균의 합)
    ratio: float | None
    direction: Direction | None
    p_value: float | None
    severity: Literal["low", "mid", "high"] | None
    note: str  # 판정 근거 한 줄 (확인할 수 없으면 그 이유)
    peak_ts: datetime | None = None  # 가장 벗어난 5분 구간
    notes: list[str] = field(default_factory=list)


def _floor(ts: datetime) -> datetime:
    ts = ts.astimezone(UTC)
    return ts.replace(minute=ts.minute - ts.minute % 5, second=0, microsecond=0)


def _by_bucket(points: list[Point]) -> dict[datetime, float]:
    out: dict[datetime, float] = {}
    for ts, value in points:
        if value is not None:
            out[_floor(ts)] = out.get(_floor(ts), 0.0) + float(value)
    return out


def window_buckets(window_from: datetime, window_to: datetime) -> list[datetime]:
    """구간 [window_from, window_to) 안에서 시작하는 5분 구간 (예전 판정과 같다).

    시작이 5분 경계가 아니면 그 앞 구간은 넣지 않는다
    (장애가 끝난 정각부터 물을 때 장애 구간이 섞이지 않게).
    """
    begin = window_from.astimezone(UTC)
    start, out = _floor(begin), []
    while start < window_to:
        if start >= begin:
            out.append(start)
        start += BUCKET
    return out


def _history(slot: datetime, history: dict[datetime, float]) -> list[float | None]:
    """지난 7일 같은 시각의 값 (없는 날은 None)."""
    return [history.get(slot - timedelta(days=d)) for d in range(1, BASELINE_DAYS + 1)]


def segments(n: int) -> list[tuple[int, int]]:
    """검정할 연속 구간 [i, j] (5분 구간 번호, 양끝 포함): 전체, 앞에 붙은 구간, 끝에 붙은 구간.

    장애는 보통 감시 구간 안에서 시작해 끝까지 이어지거나(끝에 붙음), 구간 전부터 있다가 끝난다
    (앞에 붙음). 구간 전체만 보면 정상 부분이 섞여 신호가 묽어진다. 여러 구간을 보는 만큼
    p값에 구간 수를 곱한다(본페로니).
    """
    found = {(0, n - 1)}
    for k in range(n):
        found.add((0, k))
        found.add((k, n - 1))
    return sorted(found)


def _severity(p: float, alpha: float) -> Literal["low", "mid", "high"]:
    if p < alpha / 1000:
        return "high"
    if p < alpha / 10:
        return "mid"
    return "low"


def judge_rate(
    window: list[datetime],
    rate: dict[datetime, float],
    payments: dict[datetime, float],
    settings: Settings,
) -> Judgement:
    """결제 실패율. 결제 `min_payments`건 이상인 5분 구간만 판정한다 (상승만)."""
    metric = "payment_fail_rate"
    fails_hist = n_hist = 0.0
    for slot in window:
        for day in range(1, BASELINE_DAYS + 1):
            past = slot - timedelta(days=day)
            n = payments.get(past) or 0.0
            if n:
                n_hist += n
                fails_hist += (rate.get(past) or 0.0) * n  # 기준은 실수로 더한다
    judged = [b for b in window if (payments.get(b) or 0.0) >= settings.min_payments]
    total = sum(payments.get(b) or 0.0 for b in window)
    window_fails = sum(round(rate.get(b, 0.0) * (payments.get(b) or 0.0)) for b in window)
    seen = round(window_fails / total, 4) if total else 0.0
    if n_hist == 0:
        return _unknown(metric, "지난 7일 같은 시간대 결제 기록이 없어 기준선을 정할 수 없음", seen)
    p0 = fails_hist / n_hist
    if p0 == 0:
        return _unknown(metric, "기준선 0 (지난 7일 같은 시간대 실패 0건)이라 판정할 수 없음", seen)
    if not judged:
        return _unknown(
            metric,
            f"5분 결제가 {settings.min_payments}건 미만이라 실패율을 판정하지 않음 "
            f"(구간 결제 {int(total)}건)",
            seen,
        )
    ok = {b for b in judged}
    n = int(sum(payments[b] for b in judged))
    k = int(sum(round(rate.get(b, 0.0) * payments[b]) for b in judged))
    tests = []
    for i, j in segments(len(window)):
        part = [b for b in window[i : j + 1] if b in ok]
        if not part:
            continue
        ns = int(sum(payments[b] for b in part))
        ks = int(sum(round(rate.get(b, 0.0) * payments[b]) for b in part))
        if ks / ns > p0:  # 상승만 본다
            tests.append((binom_upper(ks, ns, p0), window[i]))
    count = len(segments(len(window)))
    p_raw, peak = min(tests, default=(1.0, None))
    p_best = min(1.0, p_raw * count)
    observed = k / n
    status: Status = "anomaly" if p_best < settings.alpha and observed > p0 else "normal"
    skipped = len(window) - len(judged)
    note = f"실패 {k}/{n}건 ({observed:.1%}), 기준 {p0:.1%}, p={p_best:.2g}" + (
        f", 결제 {settings.min_payments}건 미만 5분 구간 {skipped}개는 판정 안 함"
        if skipped
        else ""
    )
    return Judgement(
        metric=metric,
        status=status,
        observed=round(observed, 4),
        baseline=round(p0, 4),
        ratio=round(observed / p0, 2),
        direction="up" if status == "anomaly" else None,
        p_value=p_best,
        severity=_severity(p_best, settings.alpha) if status == "anomaly" else None,
        note=note,
        peak_ts=peak,
    )


def judge_count(
    metric: str,
    window: list[datetime],
    values: dict[datetime, float],
    settings: Settings,
    *,
    volume_factor: float = 1.0,
) -> Judgement:
    """건수 지표. 주문 수는 감소만, 실패 주문 수·문의 수는 증가만 이상이다."""
    lams, observed = [], []
    for slot in window:
        past = [v for v in _history(slot, values) if v is not None]
        lams.append(sum(past) / len(past) if past else 0.0)
        observed.append(values.get(slot, 0.0))
    lam_total = sum(lams) * volume_factor
    obs_total = int(round(sum(observed)))
    if not any(v is not None for slot in window for v in _history(slot, values)):
        return _unknown(
            metric, "지난 7일 같은 시간대 자료가 없어 기준선을 정할 수 없음", float(obs_total)
        )
    if lam_total == 0:
        return _unknown(
            metric,
            "기준선 0 (지난 7일 같은 시간대 값이 모두 0)이라 판정할 수 없음",
            float(obs_total),
        )
    falling = metric in DROP_METRICS
    tail = poisson_lower if falling else poisson_upper
    tests = []
    for i, j in segments(len(window)):
        lam = sum(lams[i : j + 1]) * volume_factor
        obs = int(round(sum(observed[i : j + 1])))
        if lam > 0 and (obs < lam if falling else obs > lam):
            tests.append((tail(obs, lam), window[i]))
    count = len(segments(len(window)))
    p_raw, peak = min(tests, default=(1.0, None))
    p_best = min(1.0, p_raw * count)
    moved = bool(tests)
    status: Status = "anomaly" if p_best < settings.alpha and moved else "normal"
    direction: Direction | None = ("down" if falling else "up") if status == "anomaly" else None
    note = f"구간 합계 {obs_total} (기준 {lam_total:.1f}), p={p_best:.2g}"
    if volume_factor > 1:
        note += f", 주문이 평소의 {volume_factor:.2f}배라 기준도 같이 늘려 봄"
    notes = []
    if (
        metric in DROP_METRICS
        and obs_total > lam_total
        and poisson_upper(obs_total, lam_total) < settings.alpha
    ):
        notes.append(
            f"{metric} 평소보다 많음 (합계 {obs_total}, 기준 {lam_total:.1f})"
            " — 증가만으로는 이상이 아님"
        )
    return Judgement(
        metric=metric,
        status=status,
        observed=float(obs_total),
        baseline=round(lam_total, 4),
        ratio=round(obs_total / lam_total, 2),
        direction=direction,
        p_value=p_best,
        severity=_severity(p_best, settings.alpha) if status == "anomaly" else None,
        note=note,
        peak_ts=peak,
        notes=notes,
    )


def _unknown(metric: str, reason: str, observed: float = 0.0) -> Judgement:
    """판정할 수 없음. `observed`는 셀 수 있었던 값(없으면 0)이고 판정에는 쓰지 않는다."""
    return Judgement(
        metric=metric,
        status="unknown",
        observed=observed,
        baseline=0.0,
        ratio=None,
        direction=None,
        p_value=None,
        severity=None,
        note=f"확인할 수 없음: {reason}",
    )


def empty_window(
    window: list[datetime], orders: dict[datetime, float], payments: dict[datetime, float]
) -> bool:
    """구간에 주문·결제 기록이 하나도 없는지 (자료가 빠졌는지 정말 0건인지 구분할 수 없다)."""
    return not any((orders.get(b) or 0) or (payments.get(b) or 0) for b in window)


def to_buckets(points: list[Point]) -> dict[datetime, float]:
    """원시 점을 5분 구간 합계로 (shop-api는 이미 5분 구간이라 그대로다)."""
    return _by_bucket(points)
