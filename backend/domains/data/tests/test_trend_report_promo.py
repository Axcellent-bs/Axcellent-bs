"""보고서 추천 "재미 먼저, 브랜드는 끝에" (11/24). 실제 LLM은 안 부른다 (가짜).

- 방향 판정(순수): 의뢰 문장이 제품을 직접 말하면 product, 회사 설정 product면 product,
  그 밖 fun_first
- fun_first 추천: 업종 밖 재료가 둘보다 적으면 한계, 업종 연결 재료는 하나까지,
  결론이 제품·업종 말로 시작하면 다시 묻는다(#1657 다시 묻기 흐름에 이유만 더함)
- product면 지금 규칙 그대로 (재료·결론·한계가 방향 없이 돌린 것과 같다)
- 기획 넘김: facts 첫 줄 "방향: 재미 먼저 · 브랜드는 …" + promo_style·brand_exposure 두 칸
- 다른 회사의 화면 저장 값은 이 회사 보고서에 섞이지 않는다
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from data_agent.skills import trend_report as tr
from data_agent.skills import trend_report_insight as ti
from data_agent.skills import trend_report_promo as tp


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


H = _load("trend_report_helpers_promo", "test_trend_report.py")
FUN = (tp.FUN_FIRST, tp.END_CARD)
GOOD = "상황극 반전 형식으로 웃기고 끝에 채널을 기억하게 한다"


class SeqLLM:
    """추천(_Recommendation)·결론 다시 묻기(_Conclusion)에 차례로 답한다. 받은 지시를 남긴다."""

    def __init__(self, recommend: dict[str, Any], again: list[str] | None = None) -> None:
        self.recommend = recommend
        self.again = list(again or [])
        self.prompts: list[tuple[str, str]] = []

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        self.prompts.append((schema.__name__, prompt))
        if schema.__name__ == "_Conclusion":
            text = self.again.pop(0) if self.again else self.recommend["conclusion"]
            return schema.model_validate({"conclusion": text})
        return schema.model_validate(self.recommend)


def _brief(**kw: Any) -> SimpleNamespace:
    base = {"product": None, "goal": None, "format": None, "theme": None, "format_parts": []}
    return SimpleNamespace(**(base | kw))


@pytest.fixture
def report(tmp_path: Path):  # noqa: ANN201
    got = H._filled(tmp_path)  # noqa: SLF001
    # 업종 밖 근거 둘: 밈·크리에이터 계정 게시물 하나(content:1) + 요즘 유행·밈(meme:1)
    got.sections.content.posts[0].source_kind = "meme"
    return got


def _ids(report: Any) -> tuple[list[str], list[str]]:
    catalog = tr.evidence_catalog(report)
    off = sorted(tp.off_industry_ids(report) & set(catalog))
    industry = [k for k in catalog if k not in off]
    return off, industry


def _run(report: Any, llm: Any, promo: tuple[str, str] | None = FUN) -> Any:
    return asyncio.run(tr.recommendation_section(report, llm, promo=promo, company_name="캣북스"))


# ---------------------------------------------------------------- 방향 판정 (순수 함수)


@pytest.mark.parametrize(("text", "style"), [
    ("책 소개 영상 만들어 줘", "product"),
    ("신간 홍보 릴스", "product"),
    ("제품소개 쇼츠", "product"),
    ("재밌는 밈 쇼츠", "fun_first"),
    ("20대 남성이 좋아하는 상황극 영상", "fun_first"),
    ("책 읽는 일상 밈", "fun_first"),  # 제품을 말해도 소개·홍보를 하라는 말이 아니면 재미 먼저
])  # fmt: skip
def test_request_text_decides_style(text: str, style: str) -> None:
    assert tp.direction(_brief(), text, {}) == (style, "end_card")


def test_brief_fields_and_company_setting() -> None:
    assert tp.direction(_brief(format_parts=["책 소개 영상"]), None, {})[0] == "product"
    assert tp.direction(_brief(goal="신간 홍보"), None, {})[0] == "product"
    company = {"promo_style": "product", "brand_exposure": "caption"}
    assert tp.direction(_brief(), "재밌는 밈 쇼츠", company) == ("product", "caption")
    fun = {"promo_style": "fun_first", "brand_exposure": "prop"}
    assert tp.direction(_brief(), "재밌는 밈 쇼츠", fun) == ("fun_first", "prop")
    # 모르는 값은 기본값
    assert tp.direction(_brief(), None, {"promo_style": "x", "brand_exposure": "y"}) == FUN


def test_direction_line_has_no_digits() -> None:
    line = tp.direction_line("fun_first", "end_card")
    assert line == "재미 먼저 · 브랜드는 마지막에 채널 이름·로고"
    assert tp.direction_line("fun_first", "prop") == "재미 먼저 · 브랜드는 화면 속 소품으로 살짝"
    assert tp.direction_line("fun_first", "caption") == "재미 먼저 · 브랜드는 캡션·프로필에만"
    assert tp.direction_line("product", "end_card") is None
    # 지시 글에 숫자가 없다 (근거 id 목록은 코드가 넣는 값이라 빼고 본다)
    assert not any(c.isdigit() for c in tp.fun_prompt(set(), "end_card"))


def test_off_industry_sources_include_radar_audience_evidence() -> None:
    # #1681 대상층: 크리에이터 계정 게시물·조회 천만 이상·쇼츠 검색도 업종 밖 근거다
    posts = [SimpleNamespace(source_kind="benchmark", account_kind="industry"),
             SimpleNamespace(source_kind="benchmark", account_kind="creator"),
             SimpleNamespace(source_kind="meme", account_kind=None),
             SimpleNamespace(source_kind="benchmark", account_kind="brand")]  # fmt: skip
    content = SimpleNamespace(posts=posts, memes=[1], mega=[1, 2], shorts=[1])
    report = SimpleNamespace(sections=SimpleNamespace(content=content))
    assert tp.off_industry_ids(report) == {"content:2", "content:3", "meme:1", "mega:1",
                                           "mega:2", "shorts:1"}  # fmt: skip
    assert [name for name, _ in tp.OFF_INDUSTRY_SOURCES][:2] == ["밈·크리에이터 계정 게시물",
                                                                  "요즘 유행·밈"]  # fmt: skip


# ---------------------------------------------------------------- fun_first 추천


def test_fun_first_keeps_off_industry_first_and_one_industry_link(report: Any) -> None:
    off, industry = _ids(report)
    assert len(off) >= 2 and len(industry) >= 2
    llm = SeqLLM({"conclusion": GOOD, "materials": [
        {"kind": "other", "text": "끝에 채널 이름을 남긴다", "evidence": [industry[0]]},
        {"kind": "meme", "text": "요즘 뜨는 밈 분위기로", "evidence": [off[1]]},
        {"kind": "format", "text": "상황극 반전 형식", "evidence": [off[0]]},
        # 업종 연결 둘째
        {"kind": "topic", "text": "업종 해시태그 주제", "evidence": [industry[1]]},
        # 재미 먼저에 없는 종류
        {"kind": "hashtag", "text": "해시태그를 쓴다", "evidence": [off[0]]},
    ]})  # fmt: skip
    section, calls, limits = _run(report, llm)
    assert calls == 1 and section.status == "known" and section.conclusion == GOOD
    assert [m.kind for m in section.materials] == ["meme", "format", "other"]  # 업종 밖 먼저
    dropped = next(x for x in limits if x.startswith("버린 재료"))
    assert "topic: 업종 연결 재료는 하나만" in dropped
    assert "hashtag: 재미 먼저에는 쓰지 않는 종류" in dropped
    assert tp.FEW_OFF not in limits
    assert (section.promo_style, section.brand_exposure) == FUN
    prompt = llm.prompts[0][1]
    assert "재미 먼저, 브랜드는 끝에" in prompt and all(i in prompt for i in off)


def test_fun_first_with_fewer_than_two_off_industry_materials_notes_limit(report: Any) -> None:
    off, industry = _ids(report)
    llm = SeqLLM({"conclusion": GOOD, "materials": [
        {"kind": "meme", "text": "요즘 뜨는 밈 분위기로", "evidence": [off[0]]},
        {"kind": "other", "text": "끝에 채널 이름을 남긴다", "evidence": [industry[0]]},
    ]})  # fmt: skip
    section, _calls, limits = _run(report, llm)
    assert [m.kind for m in section.materials] == ["meme", "other"]  # 있는 만큼 쓴다
    assert tp.FEW_OFF in limits and section.status == "known"


@pytest.mark.parametrize("bad", ["책 읽는 일상을 밈으로 보여 준다", "캣북스 신간을 웃기게 알린다",
                                 "'서점' 상황극으로 웃긴다"])  # fmt: skip
def test_conclusion_starting_with_product_is_asked_again(report: Any, bad: str) -> None:
    off, _industry = _ids(report)
    material = {"kind": "meme", "text": "요즘 뜨는 밈 분위기로", "evidence": [off[0]]}
    llm = SeqLLM({"conclusion": bad, "materials": [material]}, again=[GOOD])
    section, calls, limits = _run(report, llm)
    assert calls == 2 and [p[0] for p in llm.prompts] == ["_Recommendation", "_Conclusion"]
    assert tp.STARTS_WITH_PRODUCT in llm.prompts[1][1]  # 버린 이유를 같이 준다
    assert "재미 먼저, 브랜드는 끝에" in llm.prompts[1][1]  # 다시 물을 때도 재미 먼저 지시
    assert section.conclusion == GOOD
    assert f"결론을 다시 받음 (처음 답: {tp.STARTS_WITH_PRODUCT})" in limits


def test_conclusion_still_product_after_retry_is_dropped(report: Any) -> None:
    off, _industry = _ids(report)
    material = {"kind": "meme", "text": "요즘 뜨는 밈 분위기로", "evidence": [off[0]]}
    llm = SeqLLM(
        {"conclusion": "책 이야기로 시작", "materials": [material]}, again=["제품을 보여 준다"]
    )
    section, calls, limits = _run(report, llm)
    assert calls == 2 and section.conclusion is None and section.status == "unknown"
    assert section.reason == tp.STARTS_WITH_PRODUCT
    assert f"결론 한 줄을 버림 ({tp.STARTS_WITH_PRODUCT})" in limits


# ---------------------------------------------------------------- product = 지금 규칙


def test_product_follows_current_rules(report: Any) -> None:
    _off, industry = _ids(report)
    answer = {"conclusion": "책 소개를 질문형 릴스로", "materials": [
        {"kind": "hashtag", "text": "해시태그를 쓴다", "evidence": [industry[0]]},
        {"kind": "writing", "text": "첫 줄을 질문으로", "evidence": [industry[1]]},
    ]}  # fmt: skip
    before, calls0, limits0 = _run(report, SeqLLM(answer), promo=None)
    llm = SeqLLM(answer)
    after, calls1, limits1 = _run(report, llm, promo=(tp.PRODUCT, tp.END_CARD))
    assert (after.conclusion, after.status, calls1, limits1) == (before.conclusion, before.status,
                                                                  calls0, limits0)  # fmt: skip
    assert [m.model_dump() for m in after.materials] == [m.model_dump() for m in before.materials]
    assert [m.kind for m in after.materials] == ["hashtag", "writing"]
    assert llm.prompts[0][1] == tr._PROMPT  # noqa: SLF001 - 재미 먼저 지시를 더하지 않는다
    assert (after.promo_style, after.brand_exposure) == ("product", "end_card")
    assert (before.promo_style, before.brand_exposure) == (None, None)


# ---------------------------------------------------------------- 기획 넘김


def test_insight_first_line_and_two_fields(report: Any) -> None:
    rec = report.sections.recommendation
    rec.promo_style, rec.brand_exposure = "fun_first", "prop"
    insight, why = ti.build_insight(report)
    assert why is None and insight is not None
    assert insight.facts[0].label == "방향"
    assert insight.facts[0].value == "재미 먼저 · 브랜드는 화면 속 소품으로 살짝"
    assert len(insight.facts) <= ti.FACTS_MAX
    assert (insight.promo_style, insight.brand_exposure) == ("fun_first", "prop")
    rec.promo_style, rec.brand_exposure = "product", "end_card"
    insight, _ = ti.build_insight(report)
    assert insight is not None and insight.facts[0].label != "방향"
    assert (insight.promo_style, insight.brand_exposure) == ("product", "end_card")
    # 예전 보고서: 칸 없음 (기획이 fun_first·end_card로 본다)
    rec.promo_style = rec.brand_exposure = None
    insight, _ = ti.build_insight(report)
    assert insight is not None and insight.facts[0].label != "방향"
    assert (insight.promo_style, insight.brand_exposure) == (None, None)


def test_insight_carries_recommended_format_cards_and_humor_situations(report: Any) -> None:
    """11/26 보고서 추천 형식 카드·웃음 재료 상황을 기획 입력에 (원문 아님, 요약·검사한 것)."""
    from ax_contracts.schemas.data import TrendHumorSituation

    report.recommended_formats = ["F-02", "F-06", "F-01", "F-03"]
    report.humor_situations = [
        TrendHumorSituation(text=f"시험 전날 계획 vs 새벽 {i}시", format_id="F-02",
                            source_kind="shorts_title")  # fmt: skip
        for i in range(7)
    ]
    insight, _ = ti.build_insight(report)
    assert insight is not None and insight.format_ids == ["F-02", "F-06", "F-01"]  # 상한 3
    assert [s.text_ko for s in insight.humor_situations][:2] == [
        "시험 전날 계획 vs 새벽 0시", "시험 전날 계획 vs 새벽 1시",
    ]  # fmt: skip
    assert len(insight.humor_situations) == 5 and insight.humor_situations[0].format_id == "F-02"
    dumped = insight.model_dump(mode="json")
    assert "source_kind" not in str(dumped["humor_situations"])  # 출처 종류는 넘기지 않는다
    # 예전 보고서: 칸이 비어 있다 (기획은 지금처럼 형식 없이)
    report.recommended_formats, report.humor_situations = [], []
    old, _ = ti.build_insight(report)
    assert old is not None and old.format_ids == [] and old.humor_situations == []


def test_insight_facts_stay_within_twelve(report: Any) -> None:
    rec = report.sections.recommendation
    rec.promo_style, rec.brand_exposure = "fun_first", "end_card"
    plain = ti.pick_facts(report.model_copy(update={"sections": report.sections.model_copy(
        update={"recommendation": rec.model_copy(update={"promo_style": None})})}))  # fmt: skip
    facts = ti.pick_facts(report)
    assert facts[0].label == "방향" and len(facts) == min(len(plain) + 1, ti.FACTS_MAX)
    assert [f.label for f in facts[1:]] == [f.label for f in plain][: ti.FACTS_MAX - 1]


# ---------------------------------------------------------------- 보고서 만들기: 회사 설정


def test_build_uses_this_company_setting_only(tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    path = tmp_path / "promo_style.json"
    monkeypatch.setenv("AX_PROMO_FILE", str(path))
    path.write_text(json.dumps({"companies": {"nubi": {"promo_style": "product",
                                                       "brand_exposure": "caption"}}}),
                    encoding="utf-8")  # fmt: skip
    monkeypatch.setenv("AX_TENANT", "catbooks")
    rec = H._filled(tmp_path / "cat").sections.recommendation  # noqa: SLF001
    # 누비투어 값이 안 섞인다
    assert (rec.promo_style, rec.brand_exposure) == ("fun_first", "end_card")
    monkeypatch.setenv("AX_TENANT", "nubi")
    rec = H._filled(tmp_path / "nubi").sections.recommendation  # noqa: SLF001
    assert (rec.promo_style, rec.brand_exposure) == ("product", "caption")
