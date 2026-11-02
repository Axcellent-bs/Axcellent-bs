"""trend_scan skill: 검색어 급상승 판정. 실제 네이버를 부르지 않는다(메모리 커넥터).

- 판정(`judge_series`): 급상승·평범·자료 부족(unknown + 이유)
- skill(`scan_trends`): 그룹마다 한 요청, 회사 기본 키워드, 커넥터 실패
- 핸들러(`trend_scan`): 완료·실패 응답 모양
"""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from ax_contracts.schemas.data import (
    TrendKeywordGroup,
    TrendScanInput,
    TrendScanOutput,
    TrendScanRequest,
)
from data_agent import handlers
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.naver_datalab import TrendResponse, TrendSeries
from data_agent.explain import _Summary
from data_agent.skills.trend_scan import (
    REQUEST_DAYS,
    NoTrendGroups,
    Settings,
    default_groups,
    judge_series,
    scan_trends,
    yesterday_kst,
)

END = date(2026, 11, 1)
START = END - timedelta(days=REQUEST_DAYS - 1)
CATBOOKS_TRENDS = (
    Path(__file__).resolve().parents[3] / "config" / "tenants" / "catbooks" / "trends.toml"
)


def _calm(day: date) -> float:
    """잔잔한 실제 키워드처럼: 평소 50 근처, 요일마다 ±5% 정도 흔들림."""
    return 50.0 * (1 + 0.05 * math.sin(day.toordinal() * 2.1))


def _series(
    name: str = "고양이",
    *,
    spike: float = 1.0,
    spike_days: int = 0,
    start: date = START,
    end: date = END,
    keep=None,  # noqa: ANN001 - 날짜 → 남길지 (빈 날 만들기)
    value=_calm,  # noqa: ANN001
) -> TrendSeries:
    points = []
    day = start
    while day <= end:
        v = value(day)
        if spike_days and day > end - timedelta(days=spike_days):
            v *= spike
        if keep is None or keep(day):
            points.append((day, v))
        day += timedelta(days=1)
    top = max((v for _, v in points), default=1.0)  # 실제 응답처럼 최댓값 100
    return TrendSeries(name, (name,), tuple((d, round(v * 100 / top, 5)) for d, v in points))


# ---------------------------------------------------------------- 판정


def test_spike_is_rising_with_its_ratio() -> None:
    verdict = judge_series(_series(spike=2.0, spike_days=3), START, END)
    assert verdict.status == "rising"
    assert verdict.ratio == pytest.approx(2.0, rel=0.1)
    assert verdict.evidence.score is not None and verdict.evidence.score >= 3.2
    assert verdict.evidence.baseline_points == 56
    assert verdict.evidence.latest_period == END


def test_calm_series_is_normal() -> None:
    verdict = judge_series(_series(), START, END)
    assert verdict.status == "normal"
    assert verdict.ratio == pytest.approx(1.0, rel=0.1)
    assert verdict.unknown_reason is None


def test_spike_before_the_window_is_not_rising() -> None:
    def value(day: date) -> float:
        boost = 3.0 if END - timedelta(days=20) <= day <= END - timedelta(days=18) else 1.0
        return _calm(day) * boost

    assert judge_series(_series(value=value), START, END).status == "normal"


def test_scale_of_the_response_does_not_matter() -> None:
    """요청마다 배수가 달라도(최댓값 100으로 다시 맞춤) 같은 판정이다."""
    a = judge_series(_series(spike=2.0, spike_days=3), START, END)
    small = _series(spike=2.0, spike_days=3)
    scaled = TrendSeries(
        small.group_name, small.keywords, tuple((d, v / 683.5) for d, v in small.points)
    )
    b = judge_series(scaled, START, END)
    assert (a.status, a.ratio, a.evidence.score) == (b.status, b.ratio, b.evidence.score)


@pytest.mark.parametrize(
    ("series", "start", "reason"),
    [
        (_series(keep=lambda d: False), START, "자료 없음"),
        (_series(start=END - timedelta(days=9)), END - timedelta(days=9), "요청 기간이 짧음"),
        (_series(keep=lambda d: d.toordinal() % 3 == 0), START, "빈 날·0이 많음"),
        (
            _series(value=lambda d: 0.0 if d < END - timedelta(days=6) else 50.0),
            START,
            "빈 날·0이 많음",
        ),
        (_series(keep=lambda d: d < END - timedelta(days=6)), START, "판정 구간 7일에 값이 없음"),
    ],
    ids=["no-data", "too-short", "sparse", "zeros", "empty-window"],
)
def test_not_enough_data_is_unknown_with_a_reason(
    series: TrendSeries, start: date, reason: str
) -> None:
    verdict = judge_series(series, start, END)
    assert verdict.status == "unknown"
    assert verdict.ratio is None
    assert reason in (verdict.unknown_reason or "")


def test_tiny_baseline_is_unknown() -> None:
    def value(day: date) -> float:
        return 100.0 if day == END else 0.001 * (1 + 0.05 * math.sin(day.toordinal()))

    verdict = judge_series(_series(value=value), START, END)
    assert verdict.status == "unknown"
    assert "기준선 값이 너무 작음" in (verdict.unknown_reason or "")


# ---------------------------------------------------------------- 화면용 값 (series·평소 범위)


def test_every_verdict_carries_its_series_and_window_start() -> None:
    series = _series(spike=2.0, spike_days=3)
    verdict = judge_series(series, START, END)
    assert [(p.day, p.value) for p in verdict.series] == list(series.points)  # 받은 값 그대로
    assert verdict.window_from == END - timedelta(days=6)
    empty = judge_series(_series(keep=lambda d: False), START, END)
    assert empty.status == "unknown" and empty.series == [] and empty.window_from is not None
    sparse = judge_series(_series(keep=lambda d: d.toordinal() % 3 == 0), START, END)
    assert len(sparse.series) == len(
        _series(keep=lambda d: d.toordinal() % 3 == 0).points
    )  # 빈 날을 채우지 않음


def test_usual_band_brackets_the_baseline() -> None:
    e = judge_series(_series(), START, END).evidence
    assert e.band_low is not None and e.band_high is not None and e.baseline_value is not None
    assert e.band_low < e.baseline_value < e.band_high


# ---------------------------------------------------------------- skill


class MemoryConnector:
    """테스트 전용: 그룹 이름 → 시계열. `fail`에 있는 그룹은 커넥터 오류."""

    def __init__(self, series: dict[str, TrendSeries], fail: set[str] | None = None) -> None:
        self.series = series
        self.fail = fail or set()
        self.queries: list[TrendScanInput] = []

    async def search_trend(self, query: TrendScanInput) -> TrendResponse:
        self.queries.append(query)
        names = [g.group_name for g in query.keyword_groups]
        if self.fail & set(names):
            raise ConnectorUnavailable("UNAVAILABLE", "네이버 검색어 트렌드에 연결할 수 없다")
        return TrendResponse(
            query.start_date, query.end_date, query.time_unit, tuple(self.series[n] for n in names)
        )


def _groups(*names: str) -> list[TrendKeywordGroup]:
    return [TrendKeywordGroup(group_name=n, keywords=[n]) for n in names]


def _scan(connector: MemoryConnector, request: TrendScanRequest, env: dict | None = None):  # noqa: ANN202
    return asyncio.run(
        scan_trends(request, connector, MockLLMClient(), env=env or {})  # type: ignore[arg-type]
    )


def test_each_group_is_its_own_request() -> None:
    connector = MemoryConnector(
        {"고양이": _series("고양이", spike=3.0, spike_days=2), "그림책": _series("그림책")}
    )
    result = _scan(
        connector, TrendScanRequest(keyword_groups=_groups("고양이", "그림책"), end_date=END)
    )
    assert [len(q.keyword_groups) for q in connector.queries] == [1, 1]
    assert {(q.start_date, q.end_date) for q in connector.queries} == {(START, END)}
    status = {r.group_name: r.status for r in result.output.results}
    assert status == {"고양이": "rising", "그림책": "normal"}
    assert result.fallback and "급상승 1개" in (result.summary or "")  # LLM 없음 → 템플릿


def test_default_groups_come_from_company_config(tmp_path: Path) -> None:
    config = tmp_path / "trends.toml"
    config.write_text(
        '[[groups]]\nname = "책 추천"\nkeywords = ["책 추천", "도서 추천"]\n', encoding="utf-8"
    )
    connector = MemoryConnector({"책 추천": _series("책 추천")})
    result = _scan(connector, TrendScanRequest(end_date=END), {"DATA_TREND_CONFIG": str(config)})
    assert connector.queries[0].keyword_groups[0].keywords == ["책 추천", "도서 추천"]
    assert result.output.results[0].group_name == "책 추천"


def test_catbooks_has_default_groups() -> None:
    groups = default_groups({"DATA_TREND_CONFIG": str(CATBOOKS_TRENDS)})
    assert groups and all(g.keywords for g in groups)


def test_no_groups_anywhere_is_refused() -> None:
    with pytest.raises(NoTrendGroups):
        _scan(MemoryConnector({}), TrendScanRequest(end_date=END))


def test_end_date_defaults_to_yesterday_in_korea() -> None:
    assert yesterday_kst(datetime(2026, 11, 1, 16, 0, tzinfo=UTC)) == date(
        2026, 11, 1
    )  # KST 11-02 01시
    assert yesterday_kst(datetime(2026, 11, 1, 14, 0, tzinfo=UTC)) == date(
        2026, 10, 31
    )  # KST 11-01 23시


def test_connector_failure_for_one_group_is_unknown_for_that_group() -> None:
    connector = MemoryConnector({"고양이": _series("고양이")}, fail={"그림책"})
    result = _scan(
        connector, TrendScanRequest(keyword_groups=_groups("고양이", "그림책"), end_date=END)
    )
    by_group = {r.group_name: r for r in result.output.results}
    assert by_group["고양이"].status == "normal"
    assert by_group["그림책"].status == "unknown"
    assert "읽지 못함 [UNAVAILABLE]" in (by_group["그림책"].unknown_reason or "")
    assert result.failed_groups == ["그림책"]


def test_connector_failure_for_every_group_raises() -> None:
    connector = MemoryConnector({}, fail={"고양이"})
    with pytest.raises(ConnectorUnavailable):
        _scan(connector, TrendScanRequest(keyword_groups=_groups("고양이"), end_date=END))


def test_rising_groups_get_their_own_sentence() -> None:
    connector = MemoryConnector(
        {"고양이": _series("고양이", spike=3.0, spike_days=2), "그림책": _series("그림책")}
    )
    result = _scan(
        connector, TrendScanRequest(keyword_groups=_groups("고양이", "그림책"), end_date=END)
    )
    by_group = {r.group_name: r for r in result.output.results}
    rising = by_group["고양이"]
    assert rising.summary and rising.summary.startswith(
        "고양이 검색이 직전 56일 평소의"
    )  # LLM 없음 → 템플릿
    assert f"{rising.ratio}배" in rising.summary
    assert by_group["그림책"].summary is None  # 평범한 그룹엔 문장을 만들지 않는다


def test_group_sentence_with_invented_numbers_falls_back() -> None:
    connector = MemoryConnector({"고양이": _series("고양이", spike=3.0, spike_days=2)})
    llm = MockLLMClient(responses={_Summary: _Summary(summary="고양이가 77배 뛰었습니다.")})
    result = asyncio.run(
        scan_trends(
            TrendScanRequest(keyword_groups=_groups("고양이"), end_date=END),
            connector,  # type: ignore[arg-type]
            llm,
            env={},
        )
    )
    (verdict,) = result.output.results
    assert "77" not in (verdict.summary or "") and result.fallback
    assert "고양이: 설명 문장을 템플릿으로 대체함: number_invalid" in result.evidence.data_summary


def test_llm_numbers_outside_the_facts_fall_back_to_the_template() -> None:
    connector = MemoryConnector({"고양이": _series("고양이", spike=3.0, spike_days=2)})
    llm = MockLLMClient(responses={_Summary: _Summary(summary="고양이가 99배 올랐습니다.")})
    result = asyncio.run(
        scan_trends(
            TrendScanRequest(keyword_groups=_groups("고양이"), end_date=END), connector, llm, env={}
        )  # type: ignore[arg-type]
    )
    assert result.fallback and result.fallback_reason == "number_invalid"
    assert "99배" not in (result.summary or "")


# ---------------------------------------------------------------- 핸들러


def _request(payload: dict) -> AgentRequest:
    meta = RequestMeta(
        context_id="ctx_trend", token="x", deadline=datetime.now(UTC) + timedelta(seconds=30)
    )
    return AgentRequest(meta=meta, skill="trend_scan", input=payload)


def _handle(monkeypatch: pytest.MonkeyPatch, connector: MemoryConnector, payload: dict):  # noqa: ANN202
    monkeypatch.setattr(handlers, "get_trend_connector", lambda: connector)
    monkeypatch.setattr(handlers, "get_llm", lambda: MockLLMClient())
    return asyncio.run(handlers.HANDLERS["trend_scan"](_request(payload)))


def test_handler_completes_with_the_contract_output(monkeypatch: pytest.MonkeyPatch) -> None:
    connector = MemoryConnector({"고양이": _series("고양이", spike=2.0, spike_days=3)})
    payload = {
        "keyword_groups": [{"group_name": "고양이", "keywords": ["고양이"]}],
        "end_date": "2026-11-01",
    }
    result = _handle(monkeypatch, connector, payload)
    assert result.status == TaskStatus.COMPLETED
    output = TrendScanOutput.model_validate(result.output)
    assert output.results[0].status == "rising"
    assert output.value_kind == "relative"
    assert result.evidence and result.evidence.queries


def test_handler_reports_unavailable_without_numbers(monkeypatch: pytest.MonkeyPatch) -> None:
    connector = MemoryConnector({}, fail={"고양이"})
    payload = {
        "keyword_groups": [{"group_name": "고양이", "keywords": ["고양이"]}],
        "end_date": "2026-11-01",
    }
    result = _handle(monkeypatch, connector, payload)
    assert result.status == TaskStatus.FAILED
    assert result.outcome == "unavailable"
    assert result.output["code"] == "UNAVAILABLE"
    assert "results" not in result.output


def test_handler_without_groups_or_config_is_invalid_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATA_TREND_CONFIG", raising=False)
    result = _handle(monkeypatch, MemoryConnector({}), {"end_date": "2026-11-01"})
    assert result.status == TaskStatus.FAILED
    assert result.outcome == "invalid_input"


def test_handler_rejects_bad_input(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _handle(monkeypatch, MemoryConnector({}), {"keyword_groups": []})
    assert result.status == TaskStatus.FAILED
    assert result.outcome == "invalid_input"


# ---------------------------------------------------------------- 요일 보정 (기본은 끔)

SATURDAY = 5


def _weekly(day: date) -> float:
    """토요일만 평소의 0.7배인 키워드 (요일 효과가 뚜렷하고 날마다 흔들림이 작다)."""
    return _calm(day) * (0.7 if day.weekday() == SATURDAY else 1.0)


def _saturday_spike(day: date) -> float:
    last_saturday = END - timedelta(days=(END.weekday() - SATURDAY) % 7)
    return _weekly(day) * (1.5 if day == last_saturday else 1.0)


def test_default_has_no_weekday_correction() -> None:
    """기본(요일 보정 끔): 토요일 1.5배는 평소의 약 1.05배로 보여 평범이다.

    #263 공책 케이스와 같은 모양이다.
    """
    assert Settings().weekday == "none"
    verdict = judge_series(_series(value=_saturday_spike), START, END)
    assert verdict.status == "normal"


@pytest.mark.parametrize("mode", ["ratio", "same_weekday"])
def test_weekday_correction_sees_a_saturday_spike(mode: str) -> None:
    """요일 보정을 켜면 같은 토요일 1.5배를 그 요일 평소와 비교해 잡는다 (깨끗한 자료에서)."""
    verdict = judge_series(_series(value=_saturday_spike), START, END, Settings(weekday=mode))
    assert verdict.status == "rising"
    assert verdict.ratio == pytest.approx(1.5, rel=0.08)


@pytest.mark.parametrize("mode", ["none", "ratio", "same_weekday"])
def test_weekday_pattern_alone_is_not_rising(mode: str) -> None:
    verdict = judge_series(_series(value=_weekly), START, END, Settings(weekday=mode))
    assert verdict.status == "normal"
