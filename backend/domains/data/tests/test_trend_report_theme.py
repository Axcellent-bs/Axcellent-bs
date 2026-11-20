"""분석 의뢰 주제(11/20, wr_ada8f37811 "10대 여성들이 좋아하는 아이돌 찾아서 책 홍보" → TR-C3FD).

가짜 LLM·가짜 데이터랩(요청마다 최댓값 100으로 맞추는 실제 동작 그대로)·가짜 저장분만 쓴다.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from ax_contracts.schemas.data import TrendReport, TrendReportInput
from data_agent import data_chat as dc
from data_agent.connectors.naver_datalab import TrendResponse, TrendSeries
from data_agent.skills import trend_report as tr
from data_agent.skills import trend_report_brief as tb
from data_agent.skills import trend_report_insight as ti
from data_agent.skills import trend_report_theme as tt
from data_agent.trend_report_store import TrendReportStore


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


H = _load("trend_report_helpers_theme", "test_trend_report.py")
NOW = H.NOW
ASK = "10대 여성들이 좋아하는 아이돌 찾아서 책 홍보하는 영상 만들어주세요"
# 진짜 검색 크기 (데이터랩은 요청마다 그 요청 안 최댓값을 100으로 준다). 0이면 값 없음
TRUE = {"아이돌": 40.0, "뉴진스": 80.0, "아이브": 60.0, "르세라핌": 20.0, "없는그룹": 0.0,
        "에스파": 100.0, "보이넥스트도어": 10.0}  # fmt: skip
RISE = {"뉴진스": 1.5}  # 최근 7일 오름


class ThemeLLM:
    def __init__(self, extract: dict[str, Any], names: list[str] | None = None) -> None:
        self.extract, self.names = extract, names or []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        self.calls.append((schema.__name__, dict(untrusted or {})))
        if schema.__name__ == "_Extracted":
            return schema.model_validate(self.extract)
        if schema.__name__ == "_Names":
            return schema.model_validate({"names": self.names})
        ids = [k.removeprefix("근거_") for k in (untrusted or {}) if k.startswith("근거_")]
        words = {"0-3": "훅", "3-15": "전환", "15-25": "증거", "25-30": "마무리"}
        return schema.model_validate({
            "conclusion": "아이돌 팬 감성으로 책을 고르는 장면을 보여 주는 영상",
            "beats": [{"slot": s, "text": f"{w} 장면", "evidence": ids[:1]}
                      for s, w in words.items()],
        })  # fmt: skip


class Datalab:
    daily_limit = 1000

    def __init__(self) -> None:
        self.calls_today = 0
        self.queries: list[list[str]] = []

    async def search_trend(self, query: Any, ages: Any = ()) -> TrendResponse:
        self.calls_today += 1
        names = [g.group_name for g in query.keyword_groups]
        self.queries.append(names)
        top = max([TRUE.get(n, 30.0) * RISE.get(n, 1.0) for n in names] + [1.0])
        series = []
        for n in names:
            v = TRUE.get(n, 30.0)
            if v <= 0:
                continue  # 값 없음 (검색량 적음)
            pts = H._points(v * 100 / top, v * RISE.get(n, 1.0) * 100 / top)
            series.append(TrendSeries(n, (n,), pts))
        return TrendResponse(query.start_date, query.end_date, "date", tuple(series))


FIND = {"product": "책", "gender": "f", "ages": ["10"], "format": "영상", "keywords": [],
        "theme": "아이돌", "theme_mode": "find"}  # fmt: skip
NAMES = ["뉴진스", "아이브", "르세라핌", "없는그룹", "아이돌", "에스파", "보이넥스트도어"]


def _build(tmp_path: Path, llm: ThemeLLM, text: str, *,
           inputs: Any = None) -> tuple[TrendReport, Datalab]:  # fmt: skip
    search = Datalab()
    deps = H._deps(tmp_path, llm, H.FakeGraph({}), search=search)
    deps.theme_inputs = inputs
    request = TrendReportInput(request_text=text)
    report = H._report(request)
    asyncio.run(tr.build(report, request, deps, TrendReportStore(), now=NOW))
    return report, search


async def _inputs() -> tuple[list[str], list[str], list[str]]:
    products = ["뉴진스 포토북", "아이브 다이어리", "소설"]
    return ["아이돌 직캠", "#뉴진스"], ["아이브 신곡 MV 반응"], products


def test_find_request_ranks_theme_candidates_and_leads_keywords(tmp_path: Path) -> None:
    llm = ThemeLLM(FIND, NAMES)
    report, search = _build(tmp_path, llm, ASK, inputs=_inputs)
    b, t = report.brief, report.sections.theme
    assert (b.theme, b.theme_mode, b.sources.theme) == ("아이돌", "find", "request_text")
    assert t.status == "known" and t.theme == "아이돌" and t.target_word == "여성 10대"
    # 묶음 기준 이름(아이돌)으로 묶음 사이 크기를 맞춤: 에스파(2번째 묶음)가 1등.
    # 뉴진스는 최근 7일이 1.5배라 28일 평균이 (21×80 + 7×120) ÷ 28 = 90
    assert [(r.name, r.level) for r in t.rows] == [("에스파", 100), ("뉴진스", 90), ("아이브", 60),
                                                   ("르세라핌", 20), ("보이넥스트도어", 10)]
    assert [d.name for d in t.dropped] == ["없는그룹"] and t.dropped[0].reason == tt.NO_VALUE
    assert t.rows[1].change_pct is not None and t.rows[1].change_pct > 0  # 뉴진스 최근 7일 오름
    by = {r.name: r for r in t.rows}
    assert by["뉴진스"].seen_in == ["radar"] and by["아이브"].seen_in == ["youtube"]
    assert by["에스파"].seen_in == ["llm"] and by["뉴진스"].products == ["뉴진스 포토북"]
    assert b.keywords[:2] == ["에스파", "뉴진스"] and b.sources.keywords == "theme"
    assert "아이돌" in b.keywords  # 주제 낱말도 키워드에 남는다
    theme_calls = [q for q in search.queries if q[0] == "아이돌"]
    assert len(theme_calls) == 2 and all(len(q) <= 5 for q in theme_calls)
    assert all(q[0] == "아이돌" for q in theme_calls)  # 묶음마다 같은 기준 이름
    assert tt.RIGHTS in report.limits
    # 추천: 주제와 상위 후보가 근거로 들어가고 결론이 주제를 다룬다
    rec_in = next(d for n, d in llm.calls if n == "_Recommendation")
    assert rec_in["의뢰_주제"] == "아이돌" and "근거_theme:1" in rec_in
    first = rec_in["근거_theme:1"]
    assert "에스파" in first and not any(ch.isdigit() for ch in first)  # LLM에는 숫자 없는 말
    assert "아이돌" in (report.summary_line or "")
    assert report.calls.llm == 3  # 의뢰문 1 + 주제 후보 1 + 추천 1


def test_theme_llm_input_has_names_only_no_captions() -> None:
    radar = ["known", {"groups": [{"name": "#뉴진스", "members": ["#뉴진스", "하입보이"],
                                   "top_posts": [{"caption": "펀딩 비밀 캡션 원문"}],
                                   "why_line": "캡션 요약"}]}, {}]  # fmt: skip
    popular = ["known", {"items": [{"keyword": "아이브"}]}, {}]
    explorer = ["ok", {"cells": [{"videos": [{"title": "아이브 신곡 MV 반응" * 5,
                                             "description": "영상 설명 원문"}]}]}, {}]  # fmt: skip
    got_radar, got_yt = tt.theme_sources(radar, popular, explorer)
    text = json.dumps([got_radar, got_yt], ensure_ascii=False)
    assert got_radar == ["#뉴진스", "하입보이"] and got_yt[0] == "아이브"
    assert "펀딩" not in text and "캡션" not in text and "설명" not in text
    assert all(len(x) <= 40 for x in got_yt)


def test_datalab_calls_are_at_most_four(tmp_path: Path) -> None:
    llm = ThemeLLM(FIND, [f"그룹{i}" for i in range(30)])
    brief = tb.input_brief(TrendReportInput(theme="아이돌", theme_mode="find", target_gender="f",
                                            target_ages=["10"]))  # fmt: skip
    search = Datalab()
    section, calls, llm_calls, _ = asyncio.run(tt.theme_section(
        brief, llm, search, H._env(tmp_path), NOW))
    assert calls <= 4 and len(search.queries) <= 4 and llm_calls == 1
    assert len(section.rows) + len(section.dropped) == tt.MAX_CANDIDATES  # 15개까지만 모음


def test_use_mode_skips_the_section_and_puts_theme_first(tmp_path: Path) -> None:
    use = {"product": "책", "format": "영상", "keywords": [], "theme": "캠핑", "theme_mode": "use"}
    report, _ = _build(tmp_path, ThemeLLM(use), "캠핑 감성으로 책 홍보하는 영상 만들어 주세요")
    assert report.sections.theme.status == "skipped"
    assert report.brief.keywords[0] == "캠핑" and report.brief.sources.keywords == "request_text"
    assert tt.RIGHTS in report.limits  # 주제가 있으면 권리 안내
    assert tr.counted_sections(report) == tr.SECTIONS  # 5칸 규칙 그대로


def test_no_theme_keeps_todays_result(tmp_path: Path) -> None:
    plain = {"product": "책", "format": "영상", "keywords": ["독서모임"]}
    report, search = _build(tmp_path, ThemeLLM(plain), "독서모임 홍보 영상 만들어 주세요")
    t = report.sections.theme
    assert (t.status, t.reason) == ("skipped", "의뢰 주제 없음")
    assert report.brief.theme is None and report.brief.sources.theme is None
    assert report.brief.keywords == ["독서모임"] and tt.RIGHTS not in report.limits
    assert all(q[0] != "아이돌" for q in search.queries)
    assert tr.counted_sections(report) == tr.SECTIONS


def test_final_status_with_theme_skipped_is_the_same_as_before() -> None:
    report = TrendReport(report_id="tr_s", status="running", created_at=NOW, caller="human",
                         brief=tb.input_brief(TrendReportInput()))  # fmt: skip
    for name in tr.SECTIONS:
        getattr(report.sections, name).status = "known"
    report.sections.theme.status = "skipped"
    assert tr.final_status(report) == "done"
    report.brief.theme_mode = "find"  # find면 주제 후보도 센다
    assert tr.final_status(report) == "partial"


def test_insight_and_chat_carry_the_theme(tmp_path: Path) -> None:
    report, _ = _build(tmp_path, ThemeLLM(FIND, NAMES), ASK, inputs=_inputs)
    insight, why = ti.build_insight(report)
    assert insight is not None, why
    labels = [f.label for f in insight.facts]
    assert any("후보 에스파" in x for x in labels[:4])  # 콘티 근거 → 주제 후보 → 나머지
    body = insight.body_md or ""
    assert "## 주제 후보 · 아이돌" in body and "에스파" in body and "권리자 허락" in body
    assert ti.numbers(body) <= ti.allowed_numbers(report, insight.facts)
    # 대화 분석 의뢰 묶음
    env = {"DATA_TREND_REPORT_DB": str(tmp_path / "reports.db")}
    store = TrendReportStore(env["DATA_TREND_REPORT_DB"])
    raw = {**report.model_dump(mode="json"), "report_id": "tr_c3fd0000"}
    store.create("tr_c3fd0000", "h", NOW.date(), {}, "orchestrator", None, raw, NOW)
    store.save("tr_c3fd0000", raw)
    store.close()
    item = dc.read_report(env, NOW)["facts"]["최근 보고서"][0]  # type: ignore[index]
    assert item["주제"] == "아이돌"
    assert [c["이름"] for c in item["주제 후보"]] == ["에스파", "뉴진스", "아이브"]


def test_chat_card_shows_the_theme() -> None:
    resolved = asyncio.run(tb.resolve(TrendReportInput(request_text=ASK), ThemeLLM(FIND)))
    card = dc.request_card(ASK, resolved, {})
    assert (card["brief"]["theme"], card["brief"]["theme_mode"]) == ("아이돌", "find")
    assert card["brief"]["keywords"][0] == "아이돌"
