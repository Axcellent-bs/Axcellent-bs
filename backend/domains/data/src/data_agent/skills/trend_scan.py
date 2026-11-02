"""trend_scan: 검색어 급상승 판정 (네이버 검색어 트렌드). 읽기 전용(grade read).

정의: **최근 56일 대비 급상승**을 본다. 계절 반복 구분은 범위 밖이다.
- 판정 구간 = 요청의 마지막 7일, 기준선 = 그 앞 56일. 둘은 **한 응답 안에서만** 비교한다
  (네이버 값은 요청마다 배수가 달라서 요청 사이 비교는 하지 않는다).
- 키워드 그룹은 한 요청에 하나씩 보낸다(큰 키워드에 작은 키워드가 눌리지 않게).
- 판정: 로그 강건 z. 기준선 log 값의 중앙값·MAD(중앙 절대 편차)로 평소 수준과 흔들림을 정하고,
  판정 구간 안 연속 구간(1~7일)의 log 평균이 평소보다 몇 칸(z) 위인지 본다. 가장 큰 z가
  `z_threshold` 이상이면 rising이다. 하락은 보지 않는다.
- 판정할 수 없으면 normal이 아니라 unknown + 이유다: 자료 없음, 기준선 자료 부족(짧거나 빈 날·0이
  많음), 기준선 값이 너무 작음, 판정 구간 자료 없음.
- 계산은 코드가 하고, LLM은 설명 문장만 쓴다(`explain` 숫자 검증 그대로).

기준값(`Settings` 기본값)은 평가 개발 케이스(`eval/trend/cases/`) 결과로 정했다.
README "trend_scan".
"""

from __future__ import annotations

import math
import statistics
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ax_agent_template.llm import LLMClient
from ax_contracts.schemas import Evidence
from ax_contracts.schemas.data import (
    TrendEvidence,
    TrendKeywordGroup,
    TrendScanInput,
    TrendScanOutput,
    TrendScanRequest,
    TrendVerdict,
)
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.naver_datalab import (
    KST,
    SOURCE,
    NaverDatalabConnector,
    TrendResponse,
    TrendSeries,
)
from data_agent.explain import explain

BASELINE_DAYS = 56
WINDOW_DAYS = 7
REQUEST_DAYS = BASELINE_DAYS + WINDOW_DAYS
MAD_TO_SIGMA = 1.4826  # 정규분포에서 MAD를 표준편차로 바꾸는 상수
TREND_CONFIG_ENV = "DATA_TREND_CONFIG"  # 회사 기본 키워드 그룹 파일 (tenant_env.py가 정한다)


@dataclass(frozen=True)
class Settings:
    """판정 기준값. 기본값은 평가 개발 케이스로 정했다(README "trend_scan 기준값")."""

    z_threshold: float = 3.2  # 이 z 이상이면 급상승
    min_baseline_points: int = 28  # 기준선(56일) 중 값이 있는(0 초과) 날이 이보다 적으면 unknown
    min_baseline_level: float = 0.01  # 기준선 중앙값이 이보다 작으면(요청 최댓값 100 기준) unknown
    mad_floor: float = 0.05  # MAD 하한 (너무 잔잔한 키워드에서 작은 흔들림이 급상승이 되지 않게)
    max_segment_days: int = WINDOW_DAYS  # 평균 낼 연속 구간 최대 길이 (1이면 하루씩만 본다)


DEFAULT_SETTINGS = Settings()


# ---------------------------------------------------------------- 판정 (순수 계산)


@dataclass(frozen=True)
class _Peak:
    z: float
    mean_log: float
    start: date
    end: date


def _unknown(group: str, reason: str, evidence: TrendEvidence | None = None) -> TrendVerdict:
    return TrendVerdict(
        group_name=group,
        status="unknown",
        unknown_reason=reason,
        evidence=evidence or TrendEvidence(),
    )


def _best_segment(
    window: list[tuple[date, float]], center: float, sigma: float, max_days: int
) -> _Peak | None:
    """판정 구간 안 연속 구간(빠진 날 없이 이어진 1~7일) 중 z가 가장 큰 것.

    z = (구간 log 평균 − 기준선 log 중앙값) ÷ (σ ÷ √일수). 여러 날 이어진 급상승은 평균을 내서
    하루 흔들림보다 잘 드러나게 한다.
    """
    best: _Peak | None = None
    for i in range(len(window)):
        for j in range(i, min(len(window), i + max_days)):
            part = window[i : j + 1]
            if (part[-1][0] - part[0][0]).days != len(part) - 1:
                break  # 빠진 날이 있으면 이어진 구간이 아니다
            mean_log = sum(math.log(v) for _, v in part) / len(part)
            z = (mean_log - center) / (sigma / math.sqrt(len(part)))
            if best is None or z > best.z:
                best = _Peak(z, mean_log, part[0][0], part[-1][0])
    return best


def judge_series(
    series: TrendSeries,
    start: date,
    end: date,
    settings: Settings = DEFAULT_SETTINGS,
) -> TrendVerdict:
    """그룹 하나 판정. `start`~`end`는 그 응답의 요청 기간이다(같은 응답 안에서만 비교)."""
    group = series.group_name
    if not series.points:
        return _unknown(group, "자료 없음: 네이버가 이 기간 값을 주지 않음 (검색량이 적음)")
    window_from = end - timedelta(days=WINDOW_DAYS - 1)
    baseline_from = max(start, window_from - timedelta(days=BASELINE_DAYS))
    baseline = [v for d, v in series.points if baseline_from <= d < window_from and v > 0]
    window = [(d, v) for d, v in series.points if window_from <= d <= end and v > 0]
    known_days = (window_from - baseline_from).days
    if len(baseline) < settings.min_baseline_points:
        why = (
            f"요청 기간이 짧음 (기준선 {known_days}일)"
            if known_days < settings.min_baseline_points
            else "빈 날·0이 많음"
        )
        return _unknown(
            group,
            f"기준선 자료 부족: 값이 있는 날 {len(baseline)}일 < {settings.min_baseline_points}일"
            f" ({why})",
            TrendEvidence(baseline_points=len(baseline)),
        )
    logs = [math.log(v) for v in baseline]
    center = statistics.median(logs)
    mad = statistics.median(abs(x - center) for x in logs)
    level = math.exp(center)
    evidence = TrendEvidence(
        baseline_value=round(level, 4),
        baseline_points=len(baseline),
        spread=round(mad, 4),
    )
    if level < settings.min_baseline_level:
        return _unknown(
            group,
            f"기준선 값이 너무 작음: 중앙값 {level:.4g} < {settings.min_baseline_level:g}",
            evidence,
        )
    if not window:
        return _unknown(group, "판정 구간 7일에 값이 없음", evidence)
    sigma = MAD_TO_SIGMA * max(mad, settings.mad_floor)
    peak = _best_segment(window, center, sigma, settings.max_segment_days)
    assert peak is not None  # window가 비어 있지 않다
    last_day, last_value = window[-1]
    rising = peak.z >= settings.z_threshold
    return TrendVerdict(
        group_name=group,
        status="rising" if rising else "normal",
        ratio=round(math.exp(peak.mean_log - center), 2),
        evidence=evidence.model_copy(
            update={
                "latest_period": last_day,
                "latest_value": round(last_value, 4),
                "score": round(peak.z, 2),
            }
        ),
    )


def judge(
    query: TrendScanInput, response: TrendResponse, settings: Settings = DEFAULT_SETTINGS
) -> TrendScanOutput:
    """한 응답 판정. 평가 채점기(`eval/trend/score.py`)가 부르는 모양이다."""
    return TrendScanOutput(
        start_date=query.start_date,
        end_date=query.end_date,
        time_unit=query.time_unit,
        source=SOURCE,
        results=[
            judge_series(s, query.start_date, query.end_date, settings) for s in response.series
        ],
    )


# ---------------------------------------------------------------- 회사 기본 키워드 그룹


def default_groups(env: Mapping[str, str]) -> list[TrendKeywordGroup]:
    """`DATA_TREND_CONFIG`(회사 폴더 `trends.toml`)의 `[[groups]]`. 없거나 틀리면 빈 목록."""
    path = env.get(TREND_CONFIG_ENV)
    if not path or not Path(path).is_file():
        return []
    try:
        with Path(path).open("rb") as f:
            raw = tomllib.load(f)
        return [
            TrendKeywordGroup(group_name=g["name"], keywords=list(g["keywords"]))
            for g in raw.get("groups", [])
        ]
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError, ValueError):
        return []


def yesterday_kst(now: datetime | None = None) -> date:
    """네이버는 하루 늦게 채워진다. 기본 판정일은 한국 날짜 어제다."""
    return ((now or datetime.now(UTC)).astimezone(KST) - timedelta(days=1)).date()


# ---------------------------------------------------------------- skill


class NoTrendGroups(ValueError):
    """볼 키워드 그룹이 없다 (입력도 회사 설정도 없음)."""


@dataclass(frozen=True)
class TrendScanResult:
    output: TrendScanOutput
    evidence: Evidence
    fallback: bool
    summary: str | None
    fallback_reason: str | None = None
    fallback_numbers: tuple[float, ...] = ()
    failed_groups: list[str] = field(default_factory=list)  # 커넥터가 실패한 그룹


def _facts(results: list[TrendVerdict]) -> dict[str, int | float | str]:
    rising = [r for r in results if r.status == "rising"]
    facts: dict[str, int | float | str] = {
        "급상승 수": len(rising),
        "그룹 수": len(results),
        "판정 불가 수": sum(r.status == "unknown" for r in results),
    }
    for i, r in enumerate(rising, start=1):
        facts[f"키워드{i}"] = r.group_name
        facts[f"배수{i}"] = r.ratio or 0.0
    return facts


async def scan_trends(
    request: TrendScanRequest,
    connector: NaverDatalabConnector,
    llm: LLMClient,
    *,
    env: Mapping[str, str],
    settings: Settings = DEFAULT_SETTINGS,
    now: datetime | None = None,
) -> TrendScanResult:
    """그룹마다 한 번씩 받아 판정한다. 커넥터가 모든 그룹에서 실패하면 `ConnectorUnavailable`."""
    groups = request.keyword_groups or default_groups(env)
    if not groups:
        raise NoTrendGroups("볼 키워드 그룹이 없다 (입력에도, 회사 설정 trends.toml에도 없음)")
    end = request.end_date or yesterday_kst(now)
    start = end - timedelta(days=REQUEST_DAYS - 1)
    results: list[TrendVerdict] = []
    failed: list[str] = []
    last_error: ConnectorUnavailable | None = None
    for group in groups:
        query = TrendScanInput(keyword_groups=[group], start_date=start, end_date=end)
        try:
            response = await connector.search_trend(query)
        except ConnectorUnavailable as error:
            last_error = error
            failed.append(group.group_name)
            results.append(
                _unknown(group.group_name, f"네이버 검색어 트렌드를 읽지 못함 [{error.code}]")
            )
            continue
        results.extend(judge(query, response, settings).results)
    if last_error is not None and len(failed) == len(groups):
        raise last_error
    output = TrendScanOutput(
        start_date=start, end_date=end, time_unit="date", source=SOURCE, results=results
    )
    explained = await explain("trend", _facts(results), llm)
    queries = [f"naver search-trend group={g.group_name} {start}~{end} date" for g in groups]
    notes = [explained.summary]
    if failed:
        notes.append("읽지 못한 그룹: " + ", ".join(failed))
    if note := explained.note():
        notes.append(note)
    return TrendScanResult(
        output=output,
        evidence=Evidence(
            queries=queries, data_summary="\n".join(notes), collected_at=datetime.now(UTC)
        ),
        fallback=explained.fallback,
        summary=explained.summary,
        fallback_reason=explained.reason,
        fallback_numbers=explained.bad_numbers,
        failed_groups=failed,
    )
