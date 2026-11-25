"""의뢰 주제로 찾은 근거 (11/26 B). 의뢰를 바꿔도 근거가 같은 세트로 반복되던 것.

- 의뢰문은 칸 값이 와도 주제·확장어(`related_terms`)를 읽는다 (같은 추출 호출 1번)
- 주제·확장어와 연결 낱말이 없는 근거는 결론 근거에서 빠지고 `excluded`(off_topic)에 남는다
- 감지 탭(keyword_trend 저장분) 급상승 중 주제 신호를 끌어오고, 끝난 급상승은 참고 행 + faded
- 주제 없는 의뢰는 지금과 같다
실제 LLM·네이버·인스타를 부르지 않는다.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from ax_contracts.schemas.data import TrendReport, TrendReportInput
from data_agent.skills import trend_report as tr
from data_agent.skills import trend_report_brief as tb
from data_agent.skills import trend_report_topic as tt

FIXTURE = Path(__file__).parent / "fixtures" / "trend_report_example.json"
TERMS = ["기말고사", "벼락치기", "시험공부", "밤샘", "수능"]


class FakeLLM:
    def __init__(self, extract: dict[str, Any]) -> None:
        self.extract = extract
        self.calls: list[str] = []

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        self.calls.append(schema.__name__)
        return schema.model_validate(self.extract)


EXTRACT = {"theme": "시험 기간", "theme_mode": "use", "keywords": ["시험 기간"],
           "related_terms": [*TERMS, "시험 기간", "#밤샘", "기말고사", "일곱번째"]}  # fmt: skip


def test_related_terms_from_text_even_when_fields_come() -> None:
    llm = FakeLLM(EXTRACT)
    request = TrendReportInput(request_text="시험 기간 대학생 공감 릴스", product="캣북스",
                               target_ages=["20"], keywords=["시험"])  # fmt: skip
    got = asyncio.run(tb.resolve(request, llm))
    assert llm.calls == ["_Extracted"] and got.llm_calls == 1  # 같은 추출 호출 1번
    # 주제 자체·같은 말(#밤샘)·겹침은 빼고 최대 6
    assert got.related_terms == [*TERMS, "일곱번째"]
    assert got.brief.theme == "시험 기간"
    # 칸 값은 그대로 폼 값 (의뢰문 추정으로 덮지 않는다), 키워드는 폼 + 확장어 (최대 5)
    assert got.brief.product == "캣북스" and got.brief.sources.product == "input"
    assert got.brief.keywords == ["시험", "기말고사", "벼락치기", "시험공부", "밤샘"]


def test_card_related_terms_are_reused_without_llm() -> None:
    llm = FakeLLM(EXTRACT)
    request = TrendReportInput(request_text="시험 기간", product="캣북스", theme="시험 기간",
                               related_terms=["기말고사"])  # fmt: skip
    got = asyncio.run(tb.resolve(request, llm))
    assert llm.calls == [] and got.llm_calls == 0
    assert got.related_terms == ["기말고사"]
    assert "기말고사" in got.brief.keywords


def test_text_only_request_puts_terms_after_keywords() -> None:
    got = asyncio.run(tb.resolve(TrendReportInput(request_text="시험 기간 릴스"), FakeLLM(EXTRACT)))
    assert got.llm_calls == 1
    assert got.brief.keywords[:3] == ["시험 기간", "기말고사", "벼락치기"]


def test_no_theme_request_has_no_terms() -> None:
    llm = FakeLLM({"keywords": ["리필 화장품"], "related_terms": ["엉뚱한 말"]})
    got = asyncio.run(tb.resolve(TrendReportInput(request_text="리필 화장품 트렌드"), llm))
    assert got.related_terms == [] and got.brief.keywords == ["리필 화장품"]


def _report(theme: str | None, related: list[str] | None = None) -> TrendReport:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    r = TrendReport.model_validate(raw)
    r.brief.theme = theme
    r.related_terms = list(related or [])
    rows = r.sections.search.rows
    rows[0].keyword, rows[0].search_change_pct, rows[0].small_base = "소설", 275.0, False
    rows[0].basis = "filtered"
    rows[1].keyword, rows[1].search_change_pct, rows[1].small_base = "기말고사", 40.0, False
    rows[1].basis = "filtered"
    r.sections.content.posts[0].account = "hyundaicard"
    r.sections.content.posts[0].caption_excerpt = "연말 카드 혜택 캐러셀"
    r.sections.content.posts[0].ratio = 197.0
    return r


SCAN = ["done", {"window": "day", "rising": [
    {"keyword": "현대카드", "status": "rising", "level": "rising", "ratio": 50.0, "faded": False},
    {"keyword": "기말고사 범위", "status": "rising", "level": "rising", "ratio": 3.2,
     "faded": False},
    {"keyword": "수능시간표", "status": "rising", "level": "rising", "ratio": 110.8, "faded": True,
     "peak_end": "2026-11-19"},
    {"keyword": "시험 기간 카페", "status": "normal", "level": "normal", "ratio": 1.1},
]}]  # fmt: skip


def test_off_topic_big_rising_leaves_conclusion_and_is_listed() -> None:
    r = _report("시험 기간", TERMS)
    tt.apply(r, SCAN)
    catalog = tr.evidence_catalog(r)
    assert "search:1" not in catalog  # 소설 +275%: 주제와 연결 낱말 없음
    assert "search:2" in catalog  # 기말고사
    assert "content:1" not in catalog  # 현대카드 197배
    reasons = {(e.evidence_id, e.reason) for e in r.excluded}
    assert ("search:1", "off_topic") in reasons and ("content:1", "off_topic") in reasons
    labels = [e.label for e in r.excluded]
    assert "소설 검색" in labels and "hyundaicard 게시물" in labels


def test_scan_topic_signal_comes_in_and_faded_is_reference_only() -> None:
    r = _report("시험 기간", TERMS)
    tt.apply(r, SCAN)
    scan = [x for x in r.topic_evidence if x.kind == "scan"]
    # 감지 탭 급상승 중 주제와 연결된 것만 (현대카드·평범은 안 끌어옴)
    assert [x.label for x in scan] == ["기말고사 범위 검색", "수능시간표 검색"]
    live, faded = scan
    assert live.evidence_id == "scan:1" and not live.reference
    assert faded.reference and faded.evidence_id is None and str(faded.peak_end) == "2026-11-19"
    assert ("수능시간표 검색", "faded") in {(e.label, e.reason) for e in r.excluded}
    catalog = tr.evidence_catalog(r)
    assert list(catalog)[0] == "scan:1"  # 주제 급상승이 근거 맨 앞
    assert not any("수능시간표" in v[0] for v in catalog.values())
    assert any(x.label == "기말고사 검색" and x.evidence_id == "search:2" for x in r.topic_evidence)


def test_no_theme_report_is_unchanged() -> None:
    r = _report(None)
    before = tr.evidence_catalog(r)
    tt.apply(r, SCAN)
    assert r.topic_evidence == [] and r.excluded == [] and r.related_terms == []
    assert tr.evidence_catalog(r) == before
    assert "search:1" in before and "content:1" in before


def test_connected_words() -> None:
    words = tt.topic_words("시험 기간", ["기말고사"])
    assert words == ["시험 기간", "시험", "기말고사"]  # "기간"은 혼자서는 주제가 아님
    assert tt.connected("#시험기간", words) and tt.connected("기말고사 범위", words)
    assert not tt.connected("소설", words) and not tt.connected("", words)
    assert not tt.connected("기간 한정 할인", words)
    assert tt.topic_words(None, ["x"]) == []
