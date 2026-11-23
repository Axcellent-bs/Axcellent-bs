"""분석 보고서 AI 추천: 시간별 콘티 대신 "기획에 넘길 재료" (11/23). 실제 LLM은 안 부른다 (가짜).

- 재료: 종류(hashtag·format·topic·meme·writing·timing·other)마다 하나, 최대 4, 근거 id는 코드가 만든
  근거 목록에서만. 숫자·시간·장면·대본 말이 든 재료와 근거 id가 없는 재료는 버린다
- recommendation known = 결론 한 줄 + 재료 하나 이상
- 기획 넘김: "## 기획에 넘길 재료"에 "재료(해시태그): …" 줄. 옛 보고서(콘티만)는 "## 콘티" 장면 줄
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from ax_contracts.schemas.data import (
    TrendBeat,
    TrendMaterial,
    TrendRecommendationSection,
    TrendReportEvidence,
)
from data_agent.skills import trend_report as tr
from data_agent.skills import trend_report_insight as ti
from data_agent.trend_report_hooks import top_evidence


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


H = _load("trend_report_helpers_materials", "test_trend_report.py")


CONCLUSION = "북토크로 책 읽는 일상을 보여 주자"


def _section(tmp_path: Path, materials: list[dict], conclusion: str = CONCLUSION):  # noqa: ANN202
    report = H._filled(tmp_path)  # noqa: SLF001
    catalog = tr.evidence_catalog(report)
    llm = H.FakeLLM(recommend={"conclusion": conclusion, "materials": materials})
    section, _calls, limits = asyncio.run(tr.recommendation_section(report, llm))
    return report, catalog, section, limits


def _ids(catalog: dict, prefix: str) -> str:
    return next(k for k in catalog if k.startswith(prefix))


def test_prompt_asks_for_materials_not_timed_scenes() -> None:
    assert "materials" in tr._PROMPT and "beats" not in tr._PROMPT  # noqa: SLF001
    assert "시간별 장면" in tr._PROMPT and "대본" in tr._PROMPT  # noqa: SLF001


@pytest.mark.parametrize("text", [
    "첫 장면에 책 더미를 보여 준다",  # 장면
    "몇 초 동안 질문 자막",  # 시간
    "대본을 질문으로 시작",  # 대본
    "오프닝 컷에 해시태그",  # 컷·오프닝
    "해시태그 3개를 쓴다",  # 숫자
    "초 안에 끝내기",
])  # fmt: skip
def test_materials_with_time_scene_or_numbers_are_dropped(text: str) -> None:
    line, why = tr.check_material(text)
    assert line is None and why


def test_plain_materials_pass_the_check() -> None:
    for text in ("#북토크 해시태그를 쓴다", "첫 줄을 질문으로", "초보 독자도 읽기 쉬운 소설 주제"):
        assert tr.check_material(text) == (text, None)


def test_materials_keep_one_per_kind_with_known_evidence(tmp_path: Path) -> None:
    report = H._filled(tmp_path)  # noqa: SLF001
    catalog = tr.evidence_catalog(report)
    tag = _ids(catalog, "hashtag:")
    search = _ids(catalog, "search:")
    _, _, section, limits = _section(tmp_path / "a", [
        {"kind": "hashtag", "text": "#북토크 해시태그를 쓴다",
         "evidence": [f"근거_{tag}", "없는:1"]},
        {"kind": "hashtag", "text": "다른 해시태그", "evidence": [tag]},  # 같은 종류 → 하나만
        {"kind": "format", "text": "릴스 형식으로", "evidence": ["없는:2"]},  # 근거 없음 → 버림
        {"kind": "topic", "text": "소설 주제로", "evidence": [search]},
        {"kind": "writing", "text": "첫 장면을 질문으로", "evidence": [tag]},  # 장면 → 버림
        {"kind": "scene", "text": "모르는 종류", "evidence": [tag]},
        {"kind": "meme", "text": "요즘 밈 분위기로", "evidence": [tag]},
        {"kind": "timing", "text": "저녁에 올린다", "evidence": [tag]},
        {"kind": "other", "text": "다섯째는 버림", "evidence": [tag]},  # 최대 4
    ])  # fmt: skip
    assert [m.kind for m in section.materials] == ["hashtag", "topic", "meme", "timing"]
    first = section.materials[0]
    assert first.evidence == [catalog[tag][0]]  # 근거 문장은 코드가 붙인다
    assert [e.id for e in first.evidence_items] == [tag]
    assert section.beats == [] and section.status == "known"
    assert section.music_note == tr.MUSIC_NOTE  # 음악 안내는 그대로
    joined = " ".join(limits)
    assert "없는 근거 id" in joined and "같은 종류는 하나만" in joined
    assert "근거 id가 없거나" in joined and "시간·장면·대본" in joined and "4개까지" in joined


def test_no_materials_means_the_recommendation_is_unknown(tmp_path: Path) -> None:
    _, catalog, section, _ = _section(tmp_path, [
        {"kind": "hashtag", "text": "첫 장면에 해시태그", "evidence": ["hashtag:1"]},
    ])  # fmt: skip
    assert section.conclusion and section.materials == []
    assert section.status == "unknown" and section.reason == "근거가 있는 재료가 하나도 없음"


def test_a_finished_report_has_materials_and_no_beats(tmp_path: Path) -> None:
    report = H._filled(tmp_path)  # noqa: SLF001 - 기본 가짜 LLM: 재료 네 개
    rec = report.sections.recommendation
    assert rec.status == "known" and len(rec.materials) == 4 and rec.beats == []
    assert any("AI는 결론 한 줄과 재료 문장만 썼다" in x for x in report.limits)
    assert not any("콘티" in x for x in report.limits)


def test_insight_passes_materials_with_their_evidence_first(tmp_path: Path) -> None:
    report = H._filled(tmp_path)  # noqa: SLF001
    insight, why = ti.build_insight(report)
    assert insight is not None, why
    body = insight.body_md or ""
    assert "## 기획에 넘길 재료" in body and "## 콘티" not in body
    assert "- 재료(해시태그): 해시태그를 쓴다" in body
    assert "- 재료(글쓰기): 첫 줄을 질문으로" in body
    assert "  - 근거: " in body
    rows = ti.row_facts(report)
    first_id = report.sections.recommendation.materials[0].evidence_items[0].id
    assert insight.facts[0].label == ti.clip(rows[first_id][0], 120)  # 추천 근거 먼저
    assert len(insight.facts) <= ti.FACTS_MAX


def _old(report: Any) -> None:
    """옛 보고서 모양: 재료 없이 콘티(beats)만."""
    rec = report.sections.recommendation
    items = [m.evidence_items[0] for m in rec.materials]
    rec.materials = []
    rec.beats = [TrendBeat(slot=s, text=t, evidence=[e.text], evidence_items=[e])  # type: ignore[arg-type]
                 for s, t, e in zip(("0-3", "3-15"), ("질문 자막으로 시작", "리필 장면"), items,
                                    strict=False)]  # fmt: skip


def test_old_reports_with_beats_still_hand_over_scene_lines(tmp_path: Path) -> None:
    report = H._filled(tmp_path)  # noqa: SLF001
    _old(report)
    insight, _ = ti.build_insight(report)
    body = insight.body_md if insight else ""
    assert "## 콘티" in (body or "") and "- 훅: 질문 자막으로 시작" in (body or "")
    assert "## 기획에 넘길 재료" not in (body or "")
    beat_id = report.sections.recommendation.beats[0].evidence_items[0].id
    assert insight is not None
    assert insight.facts[0].label == ti.clip(ti.row_facts(report)[beat_id][0], 120)
    assert top_evidence(report)  # 대화 카드 근거도 콘티에서


def test_chat_card_evidence_comes_from_materials() -> None:
    ev = TrendReportEvidence(id="hashtag:1", text="#북토크 새로 뜸", short="#북토크 새로 뜸")
    rec = TrendRecommendationSection(materials=[TrendMaterial(
        kind="hashtag", text="#북토크를 쓴다", evidence=[ev.text], evidence_items=[ev])])
    report = H._report(H.TrendReportInput(preset="eco_beauty"))  # noqa: SLF001
    report.sections.recommendation = rec
    assert top_evidence(report) == ["#북토크 새로 뜸"]


def test_materials_contract_limits() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TrendMaterial(kind="hashtag", text="가" * 81)
    with pytest.raises(ValidationError):
        TrendRecommendationSection(materials=[TrendMaterial(kind="other", text="x")] * 5)
    with pytest.raises(ValidationError):
        TrendMaterial(kind="scene", text="x")  # type: ignore[arg-type]
    assert TrendRecommendationSection().materials == []  # 옛 보고서는 빈 목록
