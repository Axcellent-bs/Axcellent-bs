"""trend_history skill: 키워드 그룹별 월별 흐름. 실제 네이버를 부르지 않는다(메모리 커넥터).

- 기간(지난달까지, 개월 수 설정), 그룹마다 한 요청·월 단위
- 작년 같은 달 대비 배수 (작년 값이 없거나 0이면 비움), 판정하지 않음
- 자료 없음·못 읽음은 그 그룹만 unknown + 이유
- 핸들러 응답 모양
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from ax_contracts.schemas.data import TrendHistoryRequest, TrendKeywordGroup, TrendScanInput
from data_agent import handlers
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.naver_datalab import TrendResponse, TrendSeries
from data_agent.skills.trend_history import (
    NO_DATA,
    add_months,
    history,
    months_setting,
    period,
    with_last_year,
)

NOW = datetime(2026, 11, 2, 6, 0, tzinfo=UTC)
CATBOOKS_TRENDS = (
    Path(__file__).resolve().parents[3] / "config" / "tenants" / "catbooks" / "trends.toml"
)


class Naver:
    """그룹 이름마다 달 → 값을 정해 주는 커넥터. 요청 기간 안의 달만 돌려준다."""

    def __init__(self, values: dict[str, dict[date, float]], fail: set[str] | None = None) -> None:
        self.values = values
        self.fail = fail or set()
        self.queries: list[TrendScanInput] = []

    async def search_trend(self, query: TrendScanInput) -> TrendResponse:
        self.queries.append(query)
        name = query.keyword_groups[0].group_name
        if name in self.fail:
            raise ConnectorUnavailable("UNAVAILABLE", "네이버 검색어 트렌드에 연결할 수 없다")
        points = tuple(
            (d, v)
            for d, v in sorted(self.values.get(name, {}).items())
            if query.start_date <= d <= query.end_date
        )
        series = TrendSeries(name, tuple(query.keyword_groups[0].keywords), points)
        return TrendResponse(query.start_date, query.end_date, query.time_unit, (series,))


def _months(first: date, values: list[float]) -> dict[date, float]:
    return {add_months(first, i): v for i, v in enumerate(values)}


def _groups(*names: str) -> list[TrendKeywordGroup]:
    return [TrendKeywordGroup(group_name=n, keywords=[n]) for n in names]


def _run(naver: Naver, env: dict[str, str] | None = None, **kw):  # noqa: ANN003, ANN202
    request = TrendHistoryRequest(**kw)
    return asyncio.run(history(request, naver, env=env or {}, now=NOW))


# ---------------------------------------------------------------- 기간·계산


def test_period_ends_with_the_last_full_month() -> None:
    assert period(None, 24, NOW) == (date(2024, 11, 1), date(2026, 10, 1))
    assert period(date(2026, 3, 15), 13, NOW) == (date(2025, 3, 1), date(2026, 3, 1))


def test_vs_last_year_is_value_over_the_same_month_last_year() -> None:
    points = [(date(2025, 1, 1), 20.0), (date(2025, 2, 1), 0.0), (date(2026, 1, 1), 50.0),
              (date(2026, 2, 1), 10.0), (date(2026, 3, 1), 30.0)]  # fmt: skip
    marked = {p.month: p for p in with_last_year(points)}
    assert marked[date(2026, 1, 1)].vs_last_year == 2.5
    assert marked[date(2026, 1, 1)].last_year_value == 20.0
    assert marked[date(2026, 2, 1)].vs_last_year is None  # 작년 값이 0 → 비움 (0으로 채우지 않음)
    assert marked[date(2026, 3, 1)].vs_last_year is None  # 작년 같은 달 값이 없음
    assert marked[date(2025, 1, 1)].vs_last_year is None


def test_months_come_from_the_company_config() -> None:
    assert months_setting({"DATA_TREND_CONFIG": str(CATBOOKS_TRENDS)}) == 24
    assert months_setting({}) == 24


# ---------------------------------------------------------------- skill


def test_each_group_is_one_monthly_request_and_no_judgement() -> None:
    first = date(2024, 11, 1)
    naver = Naver({
        "고양이": _months(first, [50.0] * 12 + [100.0] * 12),
        "그림책": _months(first, [10.0] * 24),
    })  # fmt: skip
    out = _run(naver, keyword_groups=_groups("고양이", "그림책")).output
    assert (out.start_month, out.end_month, out.months) == (first, date(2026, 10, 1), 24)
    assert out.time_unit == "month" and out.value_kind == "relative"
    assert [len(q.keyword_groups) for q in naver.queries] == [1, 1]  # 그룹마다 한 요청
    assert all(q.time_unit == "month" for q in naver.queries)
    assert naver.queries[0].end_date == date(2026, 10, 31)
    cat, book = out.groups
    assert cat.status == "known" and len(cat.points) == 24
    assert cat.latest_month == date(2026, 10, 1) and cat.latest_vs_last_year == 2.0
    assert book.latest_vs_last_year == 1.0
    assert out.naver_calls == 2
    dumped = out.model_dump_json()
    assert "rising" not in dumped and "seasonal" not in dumped  # 판정하지 않는다


def test_no_data_and_failure_are_unknown_for_that_group_only() -> None:
    naver = Naver({"고양이": _months(date(2024, 11, 1), [5.0] * 24)}, fail={"밈"})
    out = _run(naver, keyword_groups=_groups("고양이", "조용한 키워드", "밈")).output
    by = {g.group_name: g for g in out.groups}
    assert by["고양이"].status == "known"
    assert by["조용한 키워드"].status == "unknown" and by["조용한 키워드"].unknown_reason == NO_DATA
    assert by["밈"].status == "unknown"
    assert by["밈"].unknown_reason == "네이버 검색어 트렌드를 읽지 못함 [UNAVAILABLE]"
    assert by["밈"].points == []


def test_default_groups_and_months_come_from_the_company_config() -> None:
    naver = Naver({})
    out = _run(naver, env={"DATA_TREND_CONFIG": str(CATBOOKS_TRENDS)}).output
    assert [g.group_name for g in out.groups] == ["고양이", "고양이 책", "책 추천", "그림책"]
    assert out.months == 24


def test_requested_months_and_end_month() -> None:
    naver = Naver({"고양이": _months(date(2025, 3, 1), [1.0] * 13)})
    out = _run(
        naver, keyword_groups=_groups("고양이"), months=13, end_month=date(2026, 3, 9)
    ).output
    assert (out.start_month, out.end_month) == (date(2025, 3, 1), date(2026, 3, 1))
    assert out.groups[0].latest_vs_last_year == 1.0


# ---------------------------------------------------------------- 핸들러


def _request(payload: dict) -> AgentRequest:
    meta = RequestMeta(
        context_id="ctx_hist", token="x", deadline=datetime.now(UTC) + timedelta(seconds=30)
    )
    return AgentRequest(meta=meta, skill="trend_history", input=payload)


def _handle(monkeypatch: pytest.MonkeyPatch, naver: Naver, payload: dict):  # noqa: ANN202
    monkeypatch.setattr(handlers, "get_trend_connector", lambda: naver)
    return asyncio.run(handlers.HANDLERS["trend_history"](_request(payload)))


def test_handler_completes(monkeypatch: pytest.MonkeyPatch) -> None:
    groups = [{"group_name": "고양이", "keywords": ["고양이"]}]
    result = _handle(monkeypatch, Naver({}), {"keyword_groups": groups})
    assert result.status == TaskStatus.COMPLETED
    assert result.output["groups"][0]["status"] == "unknown"


def test_handler_all_failed_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    groups = [{"group_name": "고양이", "keywords": ["고양이"]}]
    result = _handle(monkeypatch, Naver({}, fail={"고양이"}), {"keyword_groups": groups})
    assert result.status == TaskStatus.FAILED and result.outcome == "unavailable"


def test_handler_rejects_too_few_months(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _handle(
        monkeypatch, Naver({}), {"months": 12}
    )  # 작년 같은 달과 비교하려면 13개월 이상
    assert result.status == TaskStatus.FAILED and result.outcome == "invalid_input"
