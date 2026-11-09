"""LLM 사용량 줄이기: skill·작업별 셈, 상한 80%부터 브리프 몫만, 같은 근거의 한 줄은 다시 묻지 않음.

실제 LLM을 부르지 않는다.
"""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from ax_agent_template.llm import LLMClient, MockLLMClient
from ax_contracts.schemas.data import DomainOpportunity, InsightEvidence, TrendInsight
from data_agent import handlers, llm_budget, llm_failure
from data_agent.llm_budget import Budget, LLMDailyCapReached
from data_agent.skills import domain_opportunity as opp
from data_agent.skills import trend_insight_brief as brief
from data_agent.stage_timing import recording

DAY = date(2026, 11, 6)
ENV = {llm_budget.CALLS_ENV: "100", llm_budget.TOKENS_ENV: "10000"}


def test_usage_is_counted_by_skill_and_task() -> None:
    b = Budget()
    b.add(1, 300, DAY, skill="trend_popular", task="분류(주제·영상)")
    b.add(1, 200, DAY, skill="trend_popular", task="분류(주제·영상)")
    b.add(1, 900, DAY, skill="trend_insight_brief", task="문장(브리프 한 줄)")
    b.add(1, 50, DAY)
    assert b.by_task(DAY) == [
        ("trend_insight_brief", "문장(브리프 한 줄)", 1, 900),
        ("trend_popular", "분류(주제·영상)", 2, 500),
        ("unknown", "unknown", 1, 50),
    ]
    assert b.usage({}, DAY).tokens == 1450  # 전체 셈은 그대로


def test_task_names_come_from_the_answer_schema() -> None:
    assert llm_budget.task_of(brief._Lines) == "문장(브리프 한 줄)"
    assert llm_budget.task_of(opp._Lines) == "문장(기회 카드 한 줄)"
    assert llm_budget.task_of(object) == "builtins"  # 모르는 모듈은 모듈 이름


def test_near_the_cap_only_the_brief_may_call(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Budget()
    monkeypatch.setattr(Budget, "today", staticmethod(lambda: DAY))
    b.add(10, 7999)  # 79.99%
    b.check(ENV, skill="trend_popular")  # 아직 괜찮다
    b.add(1, 1)  # 80%
    with pytest.raises(LLMDailyCapReached, match="80%에 닿아 브리프·기회 카드 몫만"):
        b.check(ENV, skill="trend_popular")
    b.check(ENV, skill="trend_insight_brief")  # 브리프 몫은 남아 있다
    assert b.reserving(ENV)
    with pytest.raises(LLMDailyCapReached):  # 설정으로 비율을 바꿀 수 있다
        b.check(ENV | {llm_budget.RESERVE_ENV: "0.5"}, skill="video_explorer")
    b.check(ENV | {llm_budget.RESERVE_ENV: "0.9"}, skill="video_explorer")
    b.add(0, 2000)  # 상한
    with pytest.raises(LLMDailyCapReached, match="하루 상한에 닿아"):
        b.check(ENV, skill="trend_insight_brief")
    for bad in ("0", "1.5", "x"):
        assert llm_budget.reserve_ratio({llm_budget.RESERVE_ENV: bad}) == 0.8


def test_the_wrapper_records_skill_and_task_and_defers_near_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN202
        self.record_usage(context_id, "m", 100, 20, True)
        return schema(items=[])

    monkeypatch.setattr(LLMClient, "structured", fake)
    b = Budget()
    monkeypatch.setattr(llm_budget, "BUDGET", b)

    async def go(skill: str) -> None:
        with recording(f"{skill}@precompute1", emit=lambda _: None):
            await handlers.TimedLLMClient("data").structured("p", brief._Lines)

    asyncio.run(go("trend_insight_brief"))
    [(skill, task, calls, tokens)] = b.by_task()
    assert (skill, task, calls, tokens) == ("trend_insight_brief", "문장(브리프 한 줄)", 1, 120)
    b.add(0, 400_000 * 8 // 10)  # 기본 상한의 80%

    async def deferred() -> llm_failure.Scope:
        with llm_failure.scope() as s:
            with pytest.raises(LLMDailyCapReached):
                await go("trend_popular")
        return s

    assert asyncio.run(deferred()).capped  # 미룬 결과도 짧게만 저장된다
    asyncio.run(go("trend_insight_brief"))  # 브리프는 부른다


class LineLLM(MockLLMClient):
    def __init__(self, lines: dict[str, str]) -> None:
        super().__init__()
        self.lines = lines
        self.asked: list[list[str]] = []

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        names = list(dict(untrusted or {}))
        self.asked.append(names)
        items = [{"keyword": n, "line": self.lines[n]} for n in names if n in self.lines]
        return schema.model_validate({"items": items})


def _insight(topic: str, videos: int) -> TrendInsight:
    return TrendInsight(insight_id=f"ins_{topic}", rank=1, topic=topic, line="",
                        evidence=InsightEvidence(youtube_videos=videos))  # fmt: skip


def test_brief_lines_are_reused_for_the_same_id_and_facts_across_days() -> None:
    llm = LineLLM({"롤": "롤 인기 영상 8개", "골프": "골프 인기 영상 3개"})
    first = [_insight("롤", 8), _insight("골프", 3)]
    asyncio.run(brief.write_lines(first, llm, None, []))
    assert [i.line_status for i in first] == ["llm", "llm"] and len(llm.asked) == 1
    # 다음 날 같은 근거: 다시 묻지 않는다. 근거가 바뀐 골프만 묻는다
    llm.lines["골프"] = "골프 인기 영상 5개"
    again = [_insight("롤", 8), _insight("골프", 5)]
    queries: list[str] = []
    asyncio.run(brief.write_lines(again, llm, None, queries))
    assert llm.asked[-1] == ["골프"] and len(llm.asked) == 2
    assert again[0].line == "롤 인기 영상 8개" and again[0].line_status == "llm"
    assert again[1].line == "골프 인기 영상 5개"
    assert "trend_insight_brief lines reused=1" in queries
    same = [_insight("롤", 8)]
    asyncio.run(brief.write_lines(same, llm, None, []))
    assert len(llm.asked) == 2  # 모두 저장분: LLM을 부르지 않는다


def test_fixed_lines_are_not_saved() -> None:
    llm = LineLLM({"롤": "롤 인기 영상 99개"})  # 근거에 없는 숫자 → 고정 문장
    one = [_insight("롤", 8)]
    asyncio.run(brief.write_lines(one, llm, None, []))
    assert one[0].line_status == "fixed"
    asyncio.run(brief.write_lines([_insight("롤", 8)], llm, None, []))
    assert len(llm.asked) == 2  # 저장하지 않았으니 다시 묻는다


def test_card_lines_are_reused_by_subtopic_even_when_the_card_id_has_a_new_date() -> None:
    from ax_contracts.schemas.data import OpportunityFormat

    def card(day: str) -> DomainOpportunity:
        return DomainOpportunity(opportunity_id=f"opp_{day}_x", subtopic="책추천", line="",
                                 formats=[OpportunityFormat(name="리뷰", count=4)])  # fmt: skip

    llm = LineLLM({"책추천": "책추천은 리뷰 형식이 잘 된다"})
    a = [card("20261105")]
    asyncio.run(opp.write_lines(a, llm, None, []))
    b = [card("20261106")]
    asyncio.run(opp.write_lines(b, llm, None, []))
    assert len(llm.asked) == 1 and b[0].line == a[0].line and b[0].line_status == "llm"


def test_why_lines_are_reused_across_days_for_the_same_facts(tmp_path) -> None:  # noqa: ANN001
    from data_agent.skills.trend_evidence import EvidenceStore

    store = EvidenceStore(tmp_path / "evidence.db")
    store.save_line(date(2026, 11, 5), "롤", "abc", "어제 문장")
    assert store.line(DAY, "롤", "abc") == "어제 문장"  # 날이 바뀌어도 같은 근거면 그대로
    assert store.line(DAY, "롤", "xyz") is None  # 근거가 바뀌면 다시 묻는다
    store.save_line(DAY, "롤", "abc", "오늘 문장")
    assert store.line(DAY, "롤", "abc") == "오늘 문장"  # 같은 날 것이 먼저
    store.close()


def test_source_status_shows_usage_by_task_and_reserving(monkeypatch: pytest.MonkeyPatch) -> None:
    from data_agent.skills.data_sources import llm_usage_today

    b = Budget()
    monkeypatch.setattr(llm_budget, "BUDGET", b)
    b.add(3, 8500, skill="video_explorer", task="분류(주제·영상)")
    out = llm_usage_today(ENV)
    assert [(t.skill, t.task, t.calls, t.tokens) for t in out.by_task] == [
        ("video_explorer", "분류(주제·영상)", 3, 8500)]  # fmt: skip
    assert out.reserving and out.reserve_ratio == 0.8 and "80%" in (out.note or "")


def test_brief_classifies_popular_topics_left_unclassified() -> None:
    """지금 인기가 분류를 미룬(unknown) 주제는 브리프가 자기 몫으로 분류한다."""
    from ax_contracts.schemas.data import PopularTopic
    from data_agent.skills.topic_category import CategoryResult

    c = brief.Candidate("롤", [], popular=PopularTopic(rank=1, keyword="롤",
                                                     topic_category="unknown"))  # fmt: skip
    assert c.pending()
    c.cls = CategoryResult("게임", None, 0, real_entity=True, is_person=False)
    assert c.category() == "게임" and not c.pending() and c.real_entity() is True
    golf = PopularTopic(rank=2, keyword="골프", topic_category="연예·스포츠")
    known = brief.Candidate("골프", [], popular=golf)
    known.cls = CategoryResult("게임", None, 0)
    assert known.category() == "연예·스포츠"  # 지금 인기 분류가 있으면 그대로
