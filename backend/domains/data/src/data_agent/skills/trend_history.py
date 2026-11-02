"""trend_history: 키워드 그룹별 네이버 월 단위 흐름. 읽기 전용(grade read).

- 네이버 검색어 트렌드를 월 단위(`time_unit="month"`)로, 그룹마다 한 번씩 부른다(작은 키워드가 큰
  키워드에 눌리지 않게). 기본은 회사 기본 키워드 그룹(trends.toml)·최근 `months`개월(설정, 기본 24)·
  다 채워진 지난달까지.
- **판정하지 않는다.** 급상승·계절 반복 판정은 평가가 없어서 하지 않고, 달마다 값과 "작년 같은 달
  대비" 배수(값 ÷ 작년 같은 달 값)만 코드로 계산한다. 작년 값이 없거나 0이면 배수는 None이다.
- 값은 그 그룹 요청 안 상대값(최댓값 = 100)이라 그룹끼리 높이를 비교하지 않는다.
- 자료가 없으면(네이버가 주지 않음) 그 그룹만 unknown + 이유, 못 읽으면 그 그룹만 unknown + 이유.
README "trend_history".
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from ax_contracts.schemas import Evidence
from ax_contracts.schemas.data import (
    TrendHistoryGroup,
    TrendHistoryOutput,
    TrendHistoryRequest,
    TrendKeywordGroup,
    TrendMonthPoint,
    TrendScanInput,
)
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.naver_datalab import KST, SOURCE, NaverDatalabConnector
from data_agent.skills.trend_scan import TREND_CONFIG_ENV, NoTrendGroups, default_groups

DEFAULT_MONTHS = 24
NAVER_FAILED = "네이버 검색어 트렌드를 읽지 못함"  # trend_scan과 같은 문장
NO_DATA = "네이버가 이 기간 값을 주지 않음 (검색량이 너무 적을 수 있음)"


def months_setting(env: Mapping[str, str]) -> int:
    """회사 `trends.toml` `[history] months`. 없거나 틀리면 24."""
    path = env.get(TREND_CONFIG_ENV)
    if not path or not Path(path).is_file():
        return DEFAULT_MONTHS
    try:
        with Path(path).open("rb") as f:
            value = int((tomllib.load(f).get("history") or {}).get("months", DEFAULT_MONTHS))
    except (OSError, tomllib.TOMLDecodeError, TypeError, ValueError):
        return DEFAULT_MONTHS
    return value if 13 <= value <= 60 else DEFAULT_MONTHS


def first_of_month(day: date) -> date:
    return date(day.year, day.month, 1)


def add_months(month: date, delta: int) -> date:
    total = month.year * 12 + (month.month - 1) + delta
    return date(total // 12, total % 12 + 1, 1)


def last_day(month: date) -> date:
    return date.fromordinal(add_months(month, 1).toordinal() - 1)


def period(end_month: date | None, months: int, now: datetime | None = None) -> tuple[date, date]:
    """(첫 달 1일, 마지막 달 1일). 마지막 달은 주지 않으면 지난달(한국 날짜 기준, 다 채워진 달)."""
    today = (now or datetime.now(UTC)).astimezone(KST).date()
    last = first_of_month(end_month) if end_month else add_months(first_of_month(today), -1)
    return add_months(last, -(months - 1)), last


def with_last_year(points: list[tuple[date, float]]) -> list[TrendMonthPoint]:
    """달마다 작년 같은 달 값과 배수를 붙인다 (작년 값이 없거나 0이면 None)."""
    by_month = {first_of_month(d): v for d, v in points}
    out = []
    for month in sorted(by_month):
        value = by_month[month]
        before = by_month.get(add_months(month, -12))
        ratio = round(value / before, 2) if before else None
        out.append(
            TrendMonthPoint(month=month, value=value, last_year_value=before, vs_last_year=ratio)
        )
    return out


def _unknown(group: TrendKeywordGroup, reason: str) -> TrendHistoryGroup:
    return TrendHistoryGroup(
        group_name=group.group_name,
        keywords=list(group.keywords),
        status="unknown",
        unknown_reason=reason,
    )


@dataclass(frozen=True)
class HistoryResult:
    output: TrendHistoryOutput
    evidence: Evidence


def _summary(groups: list[TrendHistoryGroup], months: int) -> str:
    known = [g for g in groups if g.status == "known"]
    text = f"키워드 그룹 {len(groups)}개의 최근 {months}개월 월별 흐름"
    unknown = len(groups) - len(known)
    if unknown:
        text += f" (자료 없음·읽지 못함 {unknown}개)"
    return text + "."


async def history(
    request: TrendHistoryRequest,
    connector: NaverDatalabConnector,
    *,
    env: Mapping[str, str],
    now: datetime | None = None,
) -> HistoryResult:
    """그룹마다 월 단위로 한 번씩 받는다. 판정하지 않는다. 상태를 바꾸지 않는다."""
    groups = request.keyword_groups or default_groups(env)
    if not groups:
        raise NoTrendGroups("볼 키워드 그룹이 없다 (입력에도, 회사 설정 trends.toml에도 없음)")
    months = request.months or months_setting(env)
    first, last = period(request.end_month, months, now)
    out_groups: list[TrendHistoryGroup] = []
    failed: list[str] = []
    for group in groups:
        query = TrendScanInput(
            keyword_groups=[group], start_date=first, end_date=last_day(last), time_unit="month"
        )
        try:
            response = await connector.search_trend(query)
        except ConnectorUnavailable as error:
            failed.append(group.group_name)
            out_groups.append(_unknown(group, f"{NAVER_FAILED} [{error.code}]"))
            continue
        series = next((s for s in response.series if s.group_name == group.group_name), None)
        points = [
            (d, v) for d, v in (series.points if series else ()) if first <= d <= last_day(last)
        ]
        if not points:
            out_groups.append(_unknown(group, NO_DATA))
            continue
        marked = with_last_year(points)
        latest = marked[-1]
        out_groups.append(
            TrendHistoryGroup(
                group_name=group.group_name,
                keywords=list(group.keywords),
                status="known",
                points=marked,
                latest_month=latest.month,
                latest_vs_last_year=latest.vs_last_year,
            )
        )
    output = TrendHistoryOutput(
        start_month=first,
        end_month=last,
        months=months,
        source=SOURCE,
        groups=out_groups,
        naver_calls=len(groups),
        summary=_summary(out_groups, months),
    )
    queries = [
        f"naver search-trend group={g.group_name} {first}~{last_day(last)} month" for g in groups
    ]
    lines = [output.summary] + (["읽지 못한 그룹: " + ", ".join(failed)] if failed else [])
    evidence = Evidence(
        queries=queries, data_summary="\n".join(lines), collected_at=now or datetime.now(UTC)
    )
    return HistoryResult(output, evidence)
