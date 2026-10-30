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
- 장애 쪽 실패 확률 기준(`judge_payment`): 거래가 적은 시간대(새벽)·약한 장애를 잡는다.
  - 실패 코드 분류는 영향 분석과 같다(`impact.split_failures`, 장애 쪽 = `FAULT_REASONS`).
  - 평소 실패율 λ: 지난 7일 같은 시각 ±1시간 5분 구간의 장애 쪽 실패 평균. 너무 작으면
    `fault_lambda_floor`로 올린다. 지난 7일 자료가 없으면 이 기준은 판정하지 않는다.
  - 5분 구간마다 p = P(X ≥ 관측 건수 | λ) (포아송 꼬리확률)
    - p < `fault_p_anomaly`이면 기준선·표본 수와 상관없이 결제 실패율 이상이다.
    - 이어진 두 구간이 모두 p < `fault_p_low`이면 두 확률을 곱해 다시 본다.
    - 이상이 아닌데 p < `fault_p_low`이면 severity "low"와 이유만 남긴다(사건 아님).
  - 연속은 구간 안에서만 본다. 앞 구간 자료가 없으면 연속이 아니다(추측하지 않는다).
  - 사용자 쪽 실패(카드 거절 등)·처음 보는 코드는 이 규칙에 넣지 않는다 (세일 오탐 방지).
- 판정할 수 없으면 "이상 없음"이 아니라 "확인할 수 없음"(`unknown`)이다.
  - 기준선이 0 (지난 7일 같은 시간대 값이 모두 0, 실패 0건)
  - 결제 건수가 모자라 실패율을 판정할 5분 구간이 없고, 장애 쪽 실패도 기준 미만
  - 구간에 주문·결제 기록이 하나도 없음 (빈 구간)
  - 지표 자료가 없음
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Literal

from data_agent import catalog
from data_agent.connectors import as_events
from data_agent.impact import split_failures

Status = Literal["normal", "anomaly", "unknown"]
Direction = Literal["up", "down"]
Point = tuple[datetime, float | None]

BASELINE_DAYS = 7
BUCKET = timedelta(minutes=5)
# 기본값은 정확도 평가(domains/data/eval)로 정했다. 회사별로 바꿀 수 있다(`settings_for`)
DEFAULT_MIN_PAYMENTS = 5
DEFAULT_ALPHA = 0.001
# 예전 고정 기준. 지금 판정은 쓰지 않는다(환경변수 호환용으로만 읽는다)
DEFAULT_FAULT_FAILURES = 2
DEFAULT_FAULT_CONSECUTIVE = 2
# 장애 쪽 실패 확률 기준 (기존 27개 평가 케이스로 정함, eval/README.md)
DEFAULT_FAULT_LAMBDA_FLOOR = 0.05  # 5분 평균 장애 쪽 실패 λ의 하한
DEFAULT_FAULT_P_ANOMALY = 0.01  # 이 확률보다 드물면 이상
DEFAULT_FAULT_P_LOW = 0.1  # 이 확률보다 드물면 low 기록, 이어진 두 구간이면 곱해서 다시 판정
FAULT_LAMBDA_SPAN = timedelta(hours=1)  # λ를 구할 때 보는 같은 시각 앞뒤 범위

# 지표의 나쁜 방향·비율 여부는 회사 카탈로그(`data_agent.catalog`)에 있다. 아래 이름은 예전 코드가
# 읽던 값(시작할 때의 카탈로그)이고, 판정은 `catalog.is_drop()`·`catalog.rate_metric_ids()`를 쓴다
RATE_METRICS = catalog.rate_metric_ids()
DROP_METRICS = frozenset(m for m in catalog.metric_ids() if catalog.is_drop(m))  # 감소만 이상
RISE_METRICS = frozenset(catalog.metric_ids()) - RATE_METRICS - DROP_METRICS  # 증가만 이상


@dataclass(frozen=True)
class Settings:
    min_payments: int = DEFAULT_MIN_PAYMENTS
    alpha: float = DEFAULT_ALPHA
    fault_failures: int = DEFAULT_FAULT_FAILURES  # 쓰지 않음 (예전 기준)
    fault_consecutive: int = DEFAULT_FAULT_CONSECUTIVE  # 쓰지 않음 (예전 기준)
    fault_lambda_floor: float = DEFAULT_FAULT_LAMBDA_FLOOR
    fault_p_anomaly: float = DEFAULT_FAULT_P_ANOMALY
    fault_p_low: float = DEFAULT_FAULT_P_LOW


def settings_for(tenant_id: str | None, env: Mapping[str, str]) -> Settings:
    """회사별 판정 설정.

    - `DATA_ANOMALY_MIN_PAYMENTS`·`DATA_ANOMALY_ALPHA`·`DATA_ANOMALY_FAULT_FAILURES`·
      `DATA_ANOMALY_FAULT_CONSECUTIVE`: 모든 회사 기본값
    - `DATA_FAULT_LAMBDA_FLOOR`·`DATA_FAULT_P_ANOMALY`·`DATA_FAULT_P_LOW`: 장애 쪽 실패 확률 기준
      (모든 회사 공통). `DATA_ANOMALY_FAULT_FAILURES`·`DATA_ANOMALY_FAULT_CONSECUTIVE`는 읽기만
      하고 판정에 쓰지 않는다(예전 기준)
    - `DATA_ANOMALY_SETTINGS`: 회사별 덮어쓰기
      (`tenant=min_payments:alpha[:fault_failures[:fault_consecutive]];…`,
      예 `shop-demo=8:0.001;big-shop=20:0.0005:10:3`). 세 번째·네 번째 값은 빼도 된다.
      형식이 틀린 항목은 무시한다.
    """
    base = Settings(
        min_payments=_int(env.get("DATA_ANOMALY_MIN_PAYMENTS"), DEFAULT_MIN_PAYMENTS),
        alpha=_float(env.get("DATA_ANOMALY_ALPHA"), DEFAULT_ALPHA),
        fault_failures=_int(env.get("DATA_ANOMALY_FAULT_FAILURES"), DEFAULT_FAULT_FAILURES),
        fault_consecutive=_int(
            env.get("DATA_ANOMALY_FAULT_CONSECUTIVE"), DEFAULT_FAULT_CONSECUTIVE
        ),
        fault_lambda_floor=_positive(
            env.get("DATA_FAULT_LAMBDA_FLOOR"), DEFAULT_FAULT_LAMBDA_FLOOR
        ),
        fault_p_anomaly=_float(env.get("DATA_FAULT_P_ANOMALY"), DEFAULT_FAULT_P_ANOMALY),
        fault_p_low=_float(env.get("DATA_FAULT_P_LOW"), DEFAULT_FAULT_P_LOW),
    )
    for item in (env.get("DATA_ANOMALY_SETTINGS") or "").split(";"):
        name, _, values = item.partition("=")
        if tenant_id and name.strip() == tenant_id:
            payments, _, rest = values.partition(":")
            alpha, _, rest = rest.partition(":")
            faults, _, consecutive = rest.partition(":")
            return replace(
                base,
                min_payments=_int(payments, base.min_payments),
                alpha=_float(alpha, base.alpha),
                fault_failures=_int(faults, base.fault_failures),
                fault_consecutive=_int(consecutive, base.fault_consecutive),
            )
    return base


def _int(text: str | None, default: int) -> int:
    try:
        value = int(str(text).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _positive(text: str | None, default: float) -> float:
    try:
        value = float(str(text).strip())
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
    peak_ts: datetime | None = None  # 가장 벗어난 연속 구간의 시작
    peak_to: datetime | None = None  # 그 구간의 끝 (다음 5분 구간 시작)
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
    metric: str | None = None,
) -> Judgement:
    """비율 지표(캣북스: 결제 실패율). 분모 `min_payments`건 이상인 5분 구간만 판정한다 (상승만).

    `metric`을 비우면 카탈로그의 첫 비율 지표다.
    """
    metric = metric or next(m for m in catalog.metric_ids() if m in catalog.rate_metric_ids())
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
    word = catalog.denominator_label(metric)  # 분모를 부르는 말 (캣북스: 결제)
    if n_hist == 0:
        return _unknown(
            metric, f"지난 7일 같은 시간대 {word} 기록이 없어 기준선을 정할 수 없음", seen
        )
    p0 = fails_hist / n_hist
    if p0 == 0:
        return _unknown(metric, "기준선 0 (지난 7일 같은 시간대 실패 0건)이라 판정할 수 없음", seen)
    if not judged:
        return _unknown(
            metric,
            f"5분 {catalog.with_subject(word)} {settings.min_payments}건 미만이라 "
            f"실패율을 판정하지 않음 (구간 {word} {int(total)}건)",
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
            tests.append((binom_upper(ks, ns, p0), window[i], window[j] + BUCKET))
    count = len(segments(len(window)))
    p_raw, peak, peak_to = min(tests, default=(1.0, None, None), key=lambda t: t[0])
    p_best = min(1.0, p_raw * count)
    observed = k / n
    status: Status = "anomaly" if p_best < settings.alpha and observed > p0 else "normal"
    skipped = len(window) - len(judged)
    note = f"실패 {k}/{n}건 ({observed:.1%}), 기준 {p0:.1%}, p={p_best:.2g}" + (
        f", {word} {settings.min_payments}건 미만 5분 구간 {skipped}개는 판정 안 함"
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
        peak_to=peak_to,
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
    falling = catalog.is_drop(metric)
    tail = poisson_lower if falling else poisson_upper
    tests = []
    for i, j in segments(len(window)):
        lam = sum(lams[i : j + 1]) * volume_factor
        obs = int(round(sum(observed[i : j + 1])))
        if lam > 0 and (obs < lam if falling else obs > lam):
            tests.append((tail(obs, lam), window[i], window[j] + BUCKET))
    count = len(segments(len(window)))
    p_raw, peak, peak_to = min(tests, default=(1.0, None, None), key=lambda t: t[0])
    p_best = min(1.0, p_raw * count)
    moved = bool(tests)
    status: Status = "anomaly" if p_best < settings.alpha and moved else "normal"
    direction: Direction | None = ("down" if falling else "up") if status == "anomaly" else None
    note = f"구간 합계 {obs_total} (기준 {lam_total:.1f}), p={p_best:.2g}"
    if volume_factor > 1:
        unit = catalog.with_subject(catalog.current().impact.unit_label)
        note += f", {unit} 평소의 {volume_factor:.2f}배라 기준도 같이 늘려 봄"
    notes = []
    if falling and obs_total > lam_total and poisson_upper(obs_total, lam_total) < settings.alpha:
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
        peak_to=peak_to,
        notes=notes,
    )


def fault_buckets(orders: list) -> dict[datetime, float]:
    """실패 주문을 5분 구간별 **장애 쪽** 실패 건수로 센다.

    분류는 영향 분석과 같은 `impact.split_failures`다. 사용자 쪽 사유(카드 거절 등)·처음 보는
    코드·사유 없음은 세지 않는다.
    """
    fault = split_failures(as_events(orders)).fault  # 실패 이벤트 또는 실패 주문 목록
    return _by_bucket([(o.at, 1.0) for o in fault])


def fault_lambda(
    bucket: datetime,
    history: dict[datetime, float],
    orders: dict[datetime, float] | None,
    floor: float,
) -> tuple[float | None, float | None]:
    """5분 구간의 평소 장애 쪽 실패율 λ: (하한을 적용한 값, 하한 전 값).

    지난 7일 같은 시각 ±`FAULT_LAMBDA_SPAN`의 5분 구간 중 주문 자료가 있는 구간(0건 기록 포함)의
    장애 쪽 실패 평균이다. 그런 구간이 하나도 없으면 (None, None) = 지난 7일 자료 없음.
    """
    if not orders:
        return None, None
    steps = int(FAULT_LAMBDA_SPAN / BUCKET)
    slots = [
        bucket - timedelta(days=day) + BUCKET * k
        for day in range(1, BASELINE_DAYS + 1)
        for k in range(-steps, steps + 1)
    ]
    seen = [t for t in slots if t in orders]
    if not seen:
        return None, None
    raw = sum(history.get(t, 0.0) for t in seen) / len(seen)
    return max(raw, floor), raw


def judge_payment(
    window: list[datetime],
    rate: dict[datetime, float],
    payments: dict[datetime, float],
    faults: dict[datetime, float] | None,
    settings: Settings,
    orders: dict[datetime, float] | None = None,
    fault_history: dict[datetime, float] | None = None,
    metric: str | None = None,
) -> Judgement:
    """비율 지표(캣북스: 결제 실패율) = 통계 판정(`judge_rate`) + 장애 쪽 실패 확률 기준.

    `faults`는 구간 안 5분 구간별 장애 쪽 실패 건수(`fault_buckets`)다. None이면 실패 코드를 읽지
    못한 것이라 통계 판정만 한다. `fault_history`는 지난 7일 같은 시각 ±1시간의 장애 쪽 실패
    건수(None이면 읽지 못함)이고, `orders`(주문 수, 지난 7일 포함)로 자료가 있는 5분 구간을 안다.

    - 5분 구간마다 λ(`fault_lambda`, 하한 `fault_lambda_floor`)와 p = P(X ≥ 관측 | λ)를 구한다.
    - p < `fault_p_anomaly`인 구간이 있으면 이상이다.
    - 이어진 두 구간이 모두 p < `fault_p_low`이면 두 p를 곱해 `fault_p_anomaly`와 다시 비교한다.
      이어짐은 `window` 안에서만 본다(구간 첫 5분은 앞과 이어졌다고 보지 않는다). 주문 시도가
      0건으로 기록된 5분 하나는 건너뛰고, 자료가 없는 5분은 연속을 끊는다(추측하지 않는다).
    - 이상이 아닌데 p < `fault_p_low`인 구간이 있으면 통계 판정을 그대로 두고 severity "low"와
      이유를 남긴다. 사건을 만들 이상이 아니다(통계 판정이 unknown이면 unknown 그대로).
    - 지난 7일 자료가 없어 λ를 정할 수 없으면 이 기준은 판정하지 않는다(통계 판정 그대로).
      장애 쪽 실패가 있으면 severity "low"로 남긴다.
    """
    base = judge_rate(window, rate, payments, settings, metric)
    if faults is None:
        return replace(base, note=base.note + "; 장애 쪽 실패 코드를 읽지 못해 통계 판정만 함")
    counts = [int(faults.get(b, 0)) for b in window]
    total = sum(counts)
    floor = settings.fault_lambda_floor
    lams = [fault_lambda(b, fault_history or {}, orders, floor) for b in window]
    if fault_history is None or all(lam is None for lam, _ in lams):
        reason = "지난 7일 같은 시각 장애 쪽 실패 자료가 없어 평소 실패율을 정할 수 없음"
        note = f"{base.note}; 장애 쪽 실패 {total}건 — {reason}"
        if total and base.status != "anomaly":
            return replace(base, severity="low", note=note)
        return replace(base, note=note)
    ps = [
        poisson_upper(c, lam) if c > 0 and lam is not None else 1.0
        for c, (lam, _) in zip(counts, lams, strict=True)
    ]
    single = [b for b, p in zip(window, ps, strict=True) if p < settings.fault_p_anomaly]
    rare = [i for i, p in enumerate(ps) if p < settings.fault_p_low]
    pairs = [
        (a, b, ps[a] * ps[b])
        for a, b in zip(rare, rare[1:], strict=False)
        if _joined(window[a], window[b], orders)
    ]
    paired = [
        window[i] for a, b, joint in pairs if joint < settings.fault_p_anomaly for i in (a, b)
    ]
    p_min = min([*ps, *(joint for _, _, joint in pairs)], default=1.0)
    known = [lam for lam, _ in lams if lam is not None]
    raw = [r for _, r in lams if r is not None]
    rule = (
        f"장애 쪽 실패 {total}건 (5분 최대 {max(counts, default=0)}건), "
        f"평소 5분 {max(raw, default=0.0):.3g}건 (하한 {floor:g} 적용 {max(known):.3g}), "
        f"가장 드문 확률 p={p_min:.2g}; p<{settings.fault_p_anomaly:g}이거나 "
        f"이어진 두 구간의 곱이 그보다 작으면 이상, p<{settings.fault_p_low:g}이면 low 기록"
    )
    hits = sorted({*single, *paired})
    if not hits:
        if not rare:
            return replace(base, note=f"{base.note}; {rule}")
        note = f"{base.note}; 장애 쪽 실패 {total}건, 드물지만 이상 아님 — {rule}"
        if base.status == "anomaly":  # 통계 판정이 이미 이상이면 그대로 둔다
            return replace(base, note=note)
        return replace(base, severity="low", note=note)
    severity: Literal["low", "mid", "high"] = (
        "high" if p_min < settings.fault_p_anomaly / 1000 else "mid"
    )
    statistical = base.status == "anomaly"
    if statistical and base.severity == "high":
        severity = "high"
    observed = base.observed
    total_payments = sum(payments.get(b) or 0.0 for b in window)
    if base.status == "unknown" and total_payments:  # 표본이 모자라도 셀 수 있었던 실패율
        fails = sum(round(rate.get(b, 0.0) * (payments.get(b) or 0.0)) for b in window)
        observed = round(fails / total_payments, 4)
    return replace(
        base,
        status="anomaly",
        observed=observed,
        ratio=round(observed / base.baseline, 2) if base.baseline else None,
        direction="up",
        severity=severity,
        note=f"{base.note.removeprefix('확인할 수 없음: ')}; {rule} → 이상",
        peak_ts=base.peak_ts if statistical else hits[0],
        peak_to=base.peak_to if statistical else hits[-1] + BUCKET,
    )


MAX_SKIPPED_EMPTY = 1  # 이어진 두 구간을 볼 때 건너뛸 수 있는 주문 0건 5분 구간 수


def _joined(last: datetime, bucket: datetime, orders: dict[datetime, float] | None) -> bool:
    gap = int((bucket - last) / BUCKET) - 1  # 사이에 낀 5분 수
    if gap == 0:
        return True
    if orders is None or gap > MAX_SKIPPED_EMPTY:
        return False
    between = [last + BUCKET * (i + 1) for i in range(gap)]
    return all(b in orders and orders[b] == 0 for b in between)


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
