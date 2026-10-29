"""anomaly_detection skill (DEP-DATA-01, 이슈 #55).

구간 [window_from, window_to)의 5분 구간을 지난 7일 같은 시간대 기준과 비교해 지표별 판정을 낸다.
숫자·판정은 코드(`metrics.judge`)가 만들고, LLM은 문장만 쓴다.
infra `verify_recovery`가 롤백 후 구간을 물어 `any_anomaly`로 복구 여부를 확인한다.

정확도 평가(PR #55) 이후 판정(자세한 규칙은 `metrics/judge.py`):
- 결제 실패율은 5분 결제가 회사별 기준(`min_payments`) 이상인 구간만 판정한다.
- 주문 수는 감소만 이상이다. 증가(세일·캠페인)는 이상이 아니고, 게시 중 공지·캠페인이 있으면
  근거에 남긴다(`campaigns`).
- 실패 주문 수는 주문이 평소보다 많으면 그만큼 기준을 늘려 본다.
- 판정할 수 없으면(기준선 0, 표본 부족, 빈 구간) 지표 `status`가 unknown이고,
  모든 지표가 unknown이면 구간 `status`도 unknown이다("확인할 수 없음").
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ax_agent_template.llm import LLMClient
from ax_contracts.schemas import Evidence
from ax_contracts.schemas.data import AnomalyDetectionInput, AnomalyDetectionOutput, MetricVerdict
from data_agent.clients.shop_client import HttpMetricSource, shop_api
from data_agent.explain import explain
from data_agent.metrics.judge import (
    BASELINE_DAYS,
    RATE_METRICS,
    Judgement,
    Settings,
    empty_window,
    judge_count,
    judge_rate,
    settings_for,
    to_buckets,
    window_buckets,
)
from data_agent.metrics.judge import _unknown as unknown_judgement
from data_agent.monitor import WATCHED_METRICS, MetricSource
from data_agent.seed_metrics import MockMetricSource

# 게시 중 공지·캠페인을 찾는 함수: (구간 시작, 구간 끝) → 한 줄씩. 없으면 빈 목록
CampaignLookup = Callable[[datetime, datetime], Awaitable[list[str]]]
EMPTY_WINDOW = "구간에 주문·결제 기록이 하나도 없음 (자료가 빠졌는지 정말 0건인지 알 수 없음)"


@dataclass(frozen=True)
class AnomalyResult:
    output: AnomalyDetectionOutput
    evidence: Evidence
    fallback: bool


def get_metric_source(env: Mapping[str, str]) -> MetricSource:
    """`DATA_MOCK=1`이면 목 공급자, 아니면 shop-api `/api/shop/metric-series` 공급자."""
    if env.get("DATA_MOCK") == "1":
        return MockMetricSource()
    return HttpMetricSource(shop_api(env))


def _floor_5m(ts: datetime) -> datetime:
    ts = ts.astimezone(UTC)
    return ts.replace(minute=ts.minute - ts.minute % 5, second=0, microsecond=0)


async def _series(source: MetricSource, metric: str, start: datetime, end: datetime) -> dict:
    try:
        return to_buckets(await source.fetch(metric, start, end))
    except ValueError:  # 이 공급자에 없는 지표(목 공급자 등): 자료 없음
        return {}


def _verdict(j: Judgement) -> MetricVerdict:
    return MetricVerdict(
        metric=j.metric,  # type: ignore[arg-type]
        observed=j.observed,
        baseline=j.baseline,
        ratio=j.ratio,
        is_anomaly=j.status == "anomaly",
        severity=j.severity,
        status=j.status,
        direction=j.direction,
        note=j.note,
    )


async def detect_anomalies(
    data: AnomalyDetectionInput,
    source: MetricSource,
    llm: LLMClient,
    *,
    settings: Settings | None = None,
    campaigns: CampaignLookup | None = None,
) -> AnomalyResult:
    settings = settings or Settings()
    metrics = list(dict.fromkeys(data.metrics)) or [name for name, _ in WATCHED_METRICS]
    start = _floor_5m(data.window_from)
    history_from = start - timedelta(days=BASELINE_DAYS)
    needed = list(dict.fromkeys([*metrics, "order_count", "payment_count"]))
    series = {m: await _series(source, m, history_from, data.window_to) for m in needed}
    window = window_buckets(data.window_from, data.window_to)

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
                judge_rate(window, series[metric], series["payment_count"], settings)
            )
        elif metric == "order_count":
            judgements.append(orders)
        elif metric == "order_fail_count":
            judgements.append(
                judge_count(metric, window, series[metric], settings, volume_factor=volume)
            )
        else:
            judgements.append(judge_count(metric, window, series[metric], settings))

    notes = [n for j in judgements for n in j.notes]
    if notes and campaigns is not None:
        found = await campaigns(data.window_from, data.window_to)
        notes.append(
            "게시 중 공지·캠페인: " + "; ".join(found) if found else "게시 중 공지·캠페인 없음"
        )

    verdicts = [_verdict(j) for j in judgements]
    abnormal = [j for j in judgements if j.status == "anomaly"]
    unknown = [j for j in judgements if j.status == "unknown"]
    any_anomaly = bool(abnormal)
    status = "anomaly" if abnormal else "unknown" if len(unknown) == len(judgements) else "normal"
    fallback = False
    if any_anomaly:
        worst = min(abnormal, key=lambda j: j.p_value if j.p_value is not None else 1.0)
        explanation = await explain(
            "anomaly",
            {
                "metric": worst.metric,
                "baseline": worst.baseline,
                "observed": worst.observed,
                "ratio": worst.ratio if worst.ratio is not None else "-",
            },
            llm,
        )
        summary, fallback = explanation.summary, explanation.fallback
    elif status == "unknown":
        reasons = sorted({j.note.removeprefix("확인할 수 없음: ") for j in unknown})
        summary = "확인할 수 없음: " + "; ".join(reasons)
    else:  # 이상이 없으면 설명할 숫자가 없어서 LLM 없이 고정 문장을 쓴다
        summary = "구간 안에서 기준선을 벗어난 지표가 없습니다."
        if unknown:
            summary += f" (확인할 수 없음: {', '.join(j.metric for j in unknown)})"

    output = AnomalyDetectionOutput(
        verdicts=verdicts,
        any_anomaly=any_anomaly,
        summary=summary,
        status=status,
        unknown_metrics=[j.metric for j in unknown],  # type: ignore[misc]
    )
    lines = [
        f"지표 {len(verdicts)}개 중 이상 {len(abnormal)}개, 확인할 수 없음 {len(unknown)}개"
        + (f" ({', '.join(j.metric for j in abnormal)})" if abnormal else ""),
        *(f"{j.metric}: {j.note}" for j in judgements),
        *notes,
        f"판정 설정: 5분 결제 {settings.min_payments}건 이상만 실패율 판정, "
        f"유의수준 {settings.alpha}",
    ]
    evidence = Evidence(
        queries=[
            f"metric_series metrics={','.join(needed)} "
            f"from={data.window_from.isoformat()} to={data.window_to.isoformat()} "
            f"baseline_days={BASELINE_DAYS}"
        ],
        data_summary="\n".join(lines),
        collected_at=datetime.now(UTC),
    )
    return AnomalyResult(output=output, evidence=evidence, fallback=fallback)


def env_settings(tenant_id: str | None, env: Mapping[str, str] = os.environ) -> Settings:
    """핸들러가 쓰는 회사별 판정 설정 (`metrics.judge.settings_for`)."""
    return settings_for(tenant_id, env)
