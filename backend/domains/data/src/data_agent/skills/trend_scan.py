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
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from ax_agent_template.llm import LLMClient
from ax_contracts.schemas import Evidence
from ax_contracts.schemas.data import (
    TrendEvidence,
    TrendKeywordGroup,
    TrendPoint,
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
from data_agent.today_board import BOARD

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
    # 요일 보정: "none"(56일 전체 중앙값·흔들림), "ratio"(요일별 평균 차이를 빼고 비교),
    # "same_weekday"(같은 요일 8일끼리만 비교). README "trend_scan 요일 보정"
    weekday: str = "none"
    min_weekday_points: int = (
        4  # 요일 보정에 쓸 같은 요일 값이 이보다 적으면 그 요일은 보정하지 않음
    )


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


def _best_segment(window: list[tuple[date, float, float]], max_days: int) -> _Peak | None:
    """판정 구간 안 연속 구간(빠진 날 없이 이어진 1~7일) 중 z가 가장 큰 것.

    `window`는 (날짜, 평소와의 차이 r = log 값 − 평소 log, 그날 흔들림 σ). z = Σ(r ÷ σ) ÷ √일수.
    σ가 모두 같으면 (구간 r 평균) ÷ (σ ÷ √일수)와 같다. 여러 날 이어진 급상승은 평균을 내서
    하루 흔들림보다 잘 드러나게 한다. `_Peak.mean_log`에는 구간 r 평균을 담는다(배수 = e^평균).
    """
    best: _Peak | None = None
    for i in range(len(window)):
        for j in range(i, min(len(window), i + max_days)):
            part = window[i : j + 1]
            if (part[-1][0] - part[0][0]).days != len(part) - 1:
                break  # 빠진 날이 있으면 이어진 구간이 아니다
            z = sum(r / sd for _, r, sd in part) / math.sqrt(len(part))
            mean_r = sum(r for _, r, _ in part) / len(part)
            if best is None or z > best.z:
                best = _Peak(z, mean_r, part[0][0], part[-1][0])
    return best


def _residuals(
    baseline: list[tuple[date, float]],
    window: list[tuple[date, float]],
    center: float,
    mad: float,
    settings: Settings,
) -> list[tuple[date, float, float]]:
    """판정 구간 날마다 (날짜, 평소와의 log 차이, 그날 흔들림 σ). 요일 보정 방식에 따라 다르다."""
    floor = settings.mad_floor
    pooled = MAD_TO_SIGMA * max(mad, floor)
    by_day: dict[int, list[float]] = {}
    for d, v in baseline:
        by_day.setdefault(d.weekday(), []).append(math.log(v))
    if settings.weekday == "same_weekday":  # 같은 요일 값끼리만 중앙값·흔들림
        out = []
        for d, v in window:
            same = by_day.get(d.weekday(), [])
            if len(same) < settings.min_weekday_points:
                out.append((d, math.log(v) - center, pooled))
                continue
            mid = statistics.median(same)
            spread = statistics.median(abs(x - mid) for x in same)
            out.append((d, math.log(v) - mid, MAD_TO_SIGMA * max(spread, floor)))
        return out
    if settings.weekday == "ratio":  # 요일별 평균 차이를 빼고 56일 전체로 비교
        effect = {
            wd: statistics.median(x - center for x in xs)
            for wd, xs in by_day.items()
            if len(xs) >= settings.min_weekday_points
        }
        adjusted = [math.log(v) - effect.get(d.weekday(), 0.0) for d, v in baseline]
        mid = statistics.median(adjusted)
        spread = statistics.median(abs(x - mid) for x in adjusted)
        sd = MAD_TO_SIGMA * max(spread, floor)
        return [(d, math.log(v) - effect.get(d.weekday(), 0.0) - mid, sd) for d, v in window]
    return [(d, math.log(v) - center, pooled) for d, v in window]


def judge_series(
    series: TrendSeries,
    start: date,
    end: date,
    settings: Settings = DEFAULT_SETTINGS,
) -> TrendVerdict:
    """그룹 하나 판정. `start`~`end`는 그 응답의 요청 기간이다(같은 응답 안에서만 비교).

    판정과 상관없이 받은 날짜별 값(`series`)과 판정 구간 시작일(`window_from`)을 붙인다
    (화면 그래프용).
    """
    verdict = _judge_core(series, start, end, settings)
    return verdict.model_copy(
        update={
            "series": [TrendPoint(day=d, value=v) for d, v in series.points],
            "window_from": end - timedelta(days=WINDOW_DAYS - 1),
        }
    )


def _judge_core(series: TrendSeries, start: date, end: date, settings: Settings) -> TrendVerdict:
    group = series.group_name
    if not series.points:
        return _unknown(group, "자료 없음: 네이버가 이 기간 값을 주지 않음 (검색량이 적음)")
    window_from = end - timedelta(days=WINDOW_DAYS - 1)
    baseline_from = max(start, window_from - timedelta(days=BASELINE_DAYS))
    baseline_days = [(d, v) for d, v in series.points if baseline_from <= d < window_from and v > 0]
    baseline = [v for _, v in baseline_days]
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
    residuals = _residuals(baseline_days, window, center, mad, settings)
    peak = _best_segment(residuals, settings.max_segment_days)
    assert peak is not None  # window가 비어 있지 않다
    last_day, last_value = window[-1]
    rising = peak.z >= settings.z_threshold
    return TrendVerdict(
        group_name=group,
        status="rising" if rising else "normal",
        ratio=round(math.exp(peak.mean_log), 2),
        evidence=evidence.model_copy(
            update={
                "latest_period": last_day,
                "latest_value": round(last_value, 4),
                "score": round(peak.z, 2),
                "band_low": round(math.exp(center - sigma), 4),
                "band_high": round(math.exp(center + sigma), 4),
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


def _read_config(env: Mapping[str, str]) -> dict[str, Any]:
    path = env.get(TREND_CONFIG_ENV)
    if not path or not Path(path).is_file():
        return {}
    try:
        with Path(path).open("rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def default_groups(env: Mapping[str, str]) -> list[TrendKeywordGroup]:
    """사람이 덮어쓴 그룹: `trends.toml`의 `[[override.groups]]`. 없거나 틀리면 빈 목록.

    옛 이름 `[[groups]]`도 같은 덮어쓰기로 읽는다(`override.groups`가 있으면 그쪽만).
    """
    try:
        raw = _read_config(env)
        found = (raw.get("override") or {}).get("groups") or raw.get("groups") or []
        return [
            TrendKeywordGroup(group_name=g["name"], keywords=list(g["keywords"])) for g in found
        ]
    except (AttributeError, KeyError, TypeError, ValueError):
        return []


def max_seed_groups(env: Mapping[str, str]) -> int:
    """씨앗 키워드로 만드는 기본 그룹 수 상한: `[scan] max_seed_groups`. 그룹마다 네이버 1번."""
    try:
        value = int((_read_config(env).get("scan") or {}).get("max_seed_groups", 5))
    except (AttributeError, TypeError, ValueError):
        return 5
    return max(1, value)


def seed_groups(seeds: Sequence[str], limit: int) -> list[TrendKeywordGroup]:
    """씨앗 키워드 하나 = 그룹 하나(이름·키워드 = 씨앗). 중복은 건너뛰고 limit개까지."""
    out: list[TrendKeywordGroup] = []
    seen: set[str] = set()
    for seed in seeds:
        word = seed.strip()
        if word and word not in seen:
            seen.add(word)
            out.append(TrendKeywordGroup(group_name=word, keywords=[word]))
    return out[:limit]


# 프로필 씨앗 키워드를 주는 함수. (씨앗 목록, 못 얻은 이유). 테스트는 LLM 없이 바꿔 끼운다
SeedProvider = Callable[[], Awaitable[tuple[Sequence[str], str | None]]]


async def resolve_groups(
    requested: Sequence[TrendKeywordGroup],
    env: Mapping[str, str],
    seed_provider: SeedProvider | None = None,
) -> list[TrendKeywordGroup]:
    """우선순위: 요청 그룹 > 사람 덮어쓰기(`[[override.groups]]`) > 프로필 씨앗 키워드.

    프로필을 못 얻으면(씨앗 없음·LLM 없음) 지어내지 않고 `TrendGroupsUnknown`.
    """
    if requested:
        return list(requested)
    if override := default_groups(env):
        return override
    reason = "프로필 씨앗 키워드를 받을 방법이 없음"
    if seed_provider is not None:
        seeds, why = await seed_provider()
        if groups := seed_groups(seeds, max_seed_groups(env)):
            return groups
        reason = why or "프로필에 씨앗 키워드가 없음"
    raise TrendGroupsUnknown(
        f"볼 키워드 그룹을 정할 수 없다: {reason} (입력에도, 사람 덮어쓰기 trends.toml에도 없음)"
    )


def yesterday_kst(now: datetime | None = None) -> date:
    """네이버는 하루 늦게 채워진다. 기본 판정일은 한국 날짜 어제다."""
    return ((now or datetime.now(UTC)).astimezone(KST) - timedelta(days=1)).date()


# ---------------------------------------------------------------- skill


class NoTrendGroups(ValueError):
    """볼 키워드 그룹이 없다 (입력도 회사 설정도 없음)."""


class TrendGroupsUnknown(NoTrendGroups):
    """요청·덮어쓰기 그룹이 없고 프로필 씨앗도 못 얻었다 (지어내지 않고 unknown)."""


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


def _group_facts(verdict: TrendVerdict) -> dict[str, int | float | str]:
    """급상승 그룹 하나의 설명용 숫자 (LLM은 이 숫자만 쓸 수 있다)."""
    e = verdict.evidence
    facts: dict[str, int | float | str] = {
        "키워드": verdict.group_name,
        "배수": verdict.ratio or 0.0,
        "기준선 일수": e.baseline_points,
        "판정 구간 일수": WINDOW_DAYS,
    }
    for name, value in (("z", e.score), ("기준선", e.baseline_value), ("최근값", e.latest_value)):
        if value is not None:
            facts[name] = value
    return facts


async def classify_verdicts(
    results: list[TrendVerdict],
    groups: Sequence[TrendKeywordGroup],
    llm: LLMClient,
    *,
    env: Mapping[str, str],
    cache: Any = None,
) -> list[TrendVerdict]:
    """판정마다 공유 주제 분류기 결과·실존 이름·광고 재료 여부를 붙인다 (판정 값은 그대로).

    묶어서 한 번 묻고 저장된 분류는 다시 묻지 않는다. 분류 못 하면 unknown + 이유, 광고 재료 None.
    """
    from data_agent.skills.ad_material import judge, load_rules
    from data_agent.skills.topic_category import TopicInput, classify_topics

    if not results:
        return results
    aliases = {g.group_name: tuple(k for k in g.keywords if k != g.group_name) for g in groups}
    try:
        found = await classify_topics(
            [TopicInput(v.group_name, aliases.get(v.group_name, ())) for v in results], llm,
            cache=cache,
        )  # fmt: skip
    except Exception as error:  # 분류는 판정과 별개라 실패해도 판정은 그대로 준다
        found = {}
        why = f"주제 분류 실패 ({type(error).__name__})"
    else:
        why = "분류 결과 없음"
    rules = load_rules(env)
    out = []
    for v in results:
        cat = found.get(v.group_name)
        category = cat.category if cat is not None else "unknown"
        ok, ok_why = judge(
            category,
            is_person=cat.is_person if cat else None,
            real_entity=cat.real_entity if cat else None,
            rules=rules,
        )
        out.append(
            v.model_copy(
                update={
                    "topic_category": category,
                    "topic_category_reason": cat.reason if cat is not None else why,
                    "real_entity": cat.real_entity if cat else None,
                    "entity_names": list(cat.entity_names) if cat else [],
                    "ad_material": ok,
                    "ad_material_reason": ok_why,
                }
            )
        )
    return out


async def scan_trends(
    request: TrendScanRequest,
    connector: NaverDatalabConnector,
    llm: LLMClient,
    *,
    env: Mapping[str, str],
    settings: Settings = DEFAULT_SETTINGS,
    now: datetime | None = None,
    category_cache: Any = None,
    seed_provider: SeedProvider | None = None,
    explain_cache: Any = None,
) -> TrendScanResult:
    """그룹마다 한 번씩 받아 판정한다. 커넥터가 모든 그룹에서 실패하면 `ConnectorUnavailable`.

    `explain_cache`를 주면 같은 숫자의 설명 문장을 다시 쓴다 (LLM을 다시 부르지 않음).

    그룹은 요청 > 사람 덮어쓰기 > 프로필 씨앗(`seed_provider`) 순 (`resolve_groups`).
    """
    groups = await resolve_groups(request.keyword_groups, env, seed_provider)
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
        judged = judge(query, response, settings).results
        for verdict in judged:
            if verdict.status != "unknown":
                BOARD.record_verdict([group.group_name, *group.keywords], verdict)
        results.extend(judged)
    if last_error is not None and len(failed) == len(groups):
        raise last_error
    results = await classify_verdicts(results, groups, llm, env=env, cache=category_cache)
    group_notes: list[str] = []
    group_fallback = False
    for i, verdict in enumerate(results):
        if verdict.status != "rising":
            continue
        said = await explain("trend_group", _group_facts(verdict), llm, explain_cache)
        results[i] = verdict.model_copy(update={"summary": said.summary})
        group_fallback = group_fallback or said.fallback
        if note := said.note():
            group_notes.append(f"{verdict.group_name}: {note}")
    output = TrendScanOutput(
        start_date=start, end_date=end, time_unit="date", source=SOURCE, results=results
    )
    explained = await explain("trend", _facts(results), llm, explain_cache)
    queries = [f"naver search-trend group={g.group_name} {start}~{end} date" for g in groups]
    notes = [explained.summary]
    if failed:
        notes.append("읽지 못한 그룹: " + ", ".join(failed))
    if note := explained.note():
        notes.append(note)
    notes.extend(group_notes)
    return TrendScanResult(
        output=output,
        evidence=Evidence(
            queries=queries, data_summary="\n".join(notes), collected_at=datetime.now(UTC)
        ),
        fallback=explained.fallback or group_fallback,
        summary=explained.summary,
        fallback_reason=explained.reason,
        fallback_numbers=explained.bad_numbers,
        failed_groups=failed,
    )
