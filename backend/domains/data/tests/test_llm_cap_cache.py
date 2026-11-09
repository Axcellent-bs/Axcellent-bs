"""LLM 하루 상한에 닿아 덜 만든 결과(브리프·기회 카드·예시 아이디어)는 하루 캐시가 아니라 짧게만
(`DATA_LLM_CAP_CACHE_MINUTES`, 기본 1시간) 저장한다. 만료되면 다음 미리 계산이 다시 만든다.

수집 계획·도메인 프로필은 상한이면 상태가 unknown이라 원래 저장하지 않는다 (여기서는 그대로인지 봄).
실제 LLM을 부르지 않는다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ax_agent_template.llm import LLMClient, MockLLMClient
from ax_contracts.schemas.data import (
    PopularTopic,
    PromoConceptsOutput,
    TrendAgeOutput,
    TrendInsightBriefRequest,
    TrendPopularOutput,
)
from data_agent import handlers, llm_budget, llm_failure
from data_agent.llm_budget import LLMDailyCapReached
from data_agent.skills import trend_insight_brief as brief
from data_agent.skills.promo_concepts import PromoStore

NOW = datetime(2026, 11, 6, 0, 30, tzinfo=UTC)  # 한국 09:30
DAY = date(2026, 11, 6)


class CappedLLM(MockLLMClient):
    """data LLM 래퍼처럼: 부르기 전에 상한을 보고 capped를 표시한 뒤 올린다."""

    def __init__(self, capped: bool = True) -> None:
        super().__init__()
        self.capped = capped
        self.asked = 0

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        self.asked += 1
        if self.capped:
            error = LLMDailyCapReached("LLM 하루 상한에 닿아 (오늘 호출 200/200회)")
            llm_failure.mark(error)
            raise error
        return schema(items=[])

    async def embed(self, texts, context_id=None):  # noqa: ANN001, ANN201
        return [[0.0, 1.0] for _ in texts]


POPULAR = TrendPopularOutput(items=[
    PopularTopic(rank=1, keyword="리그오브레전드", topic_category="게임", brand_safe=True,
                 youtube_videos=8, views_per_hour=1000.0),
])  # fmt: skip


async def _ages(keywords: list[str]) -> TrendAgeOutput:
    return TrendAgeOutput(status="known", items=[])


def _brief(tmp_path: Path, llm: CappedLLM, now: datetime, env_extra: dict[str, str] | None = None):  # noqa: ANN202
    env = {brief.DB_ENV: str(tmp_path / "brief.db"), **(env_extra or {})}

    async def go():  # noqa: ANN202
        with llm_failure.scope():  # 핸들러와 같은 범위
            return await brief.make_brief(
                TrendInsightBriefRequest(), llm, env=env, popular=POPULAR, popular_reason=None,
                rising=[], age_fn=_ages, promo=None, catalog=None, now=now,
            )  # fmt: skip

    return asyncio.run(go()).output


def test_cap_is_marked_apart_from_failures_and_reaches_the_outer_scope() -> None:
    with llm_failure.scope() as outer:
        with llm_failure.scope() as inner:
            llm_failure.mark(LLMDailyCapReached("cap"))
            assert llm_failure.capped() and not llm_failure.failed()
        assert inner.capped and not inner.failed
    assert outer.capped and not outer.failed
    assert not llm_failure.capped()  # 범위 밖은 깨끗하다


def test_cap_ttl_setting() -> None:
    assert llm_failure.cap_ttl({}) == timedelta(hours=1)
    assert llm_failure.cap_ttl({llm_failure.CAP_TTL_ENV: "15"}) == timedelta(minutes=15)
    for bad in ("0", "-3", "x"):
        assert llm_failure.cap_ttl({llm_failure.CAP_TTL_ENV: bad}) == timedelta(hours=1)


def test_the_data_llm_wrapper_marks_the_cap_before_calling(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    async def real(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN001
        called.append("llm")

    monkeypatch.setattr(LLMClient, "structured", real)
    full = llm_budget.Budget()
    full.add(10**6, 10**9)
    monkeypatch.setattr(llm_budget, "BUDGET", full)

    async def go() -> llm_failure.Scope:
        with llm_failure.scope() as s:
            with pytest.raises(LLMDailyCapReached):
                await handlers.TimedLLMClient("data").structured("p", object)
        return s

    s = asyncio.run(go())
    assert s.capped and not s.failed and called == []  # 부르지 않았다


def test_a_capped_brief_is_kept_only_for_the_short_ttl(tmp_path: Path) -> None:
    llm = CappedLLM()
    first = _brief(tmp_path, llm, NOW)
    assert any("LLM 하루 상한에 닿아 덜 만든 브리프라 60분만 저장" in x for x in first.limits)
    asked = llm.asked
    again = _brief(tmp_path, llm, NOW + timedelta(minutes=30))
    assert again.cached and llm.asked == asked  # 1시간 안이면 저장분
    # 상한이 풀린 뒤 만료되면 다시 만들고, 이번엔 하루 캐시로 저장한다
    fresh = CappedLLM(capped=False)
    later = _brief(tmp_path, fresh, NOW + timedelta(minutes=61))
    assert not later.cached and fresh.asked > 0
    assert not any("분만 저장" in x for x in later.limits)
    assert _brief(tmp_path, fresh, NOW + timedelta(hours=5)).cached  # 하루 캐시


def test_capped_brief_ttl_follows_the_setting(tmp_path: Path) -> None:
    llm = CappedLLM()
    out = _brief(tmp_path, llm, NOW, {llm_failure.CAP_TTL_ENV: "10"})
    assert any("10분만 저장" in x for x in out.limits)
    store = brief.BriefStore(tmp_path / "brief.db")
    rules = brief.rules_version(brief.load_config({}))
    assert store.get(DAY, rules, NOW + timedelta(minutes=9)) is not None
    assert store.get(DAY, rules, NOW + timedelta(minutes=10)) is None
    store.close()


def test_a_full_brief_is_still_a_day_cache(tmp_path: Path) -> None:
    llm = CappedLLM(capped=False)
    out = _brief(tmp_path, llm, NOW)
    assert not any("분만 저장" in x for x in out.limits)
    assert _brief(tmp_path, llm, NOW + timedelta(hours=10)).cached


def test_capped_example_ideas_expire_and_older_full_ones_stay(tmp_path: Path) -> None:
    store = PromoStore(tmp_path / "promo.db")
    full = PromoConceptsOutput(run_id="full", generated_at=NOW)
    capped = PromoConceptsOutput(run_id="capped", generated_at=NOW)
    store.save(DAY, full, "r")
    store.save(DAY, capped, "r", expires_at=NOW + timedelta(hours=1))
    assert store.latest(DAY, "r", NOW + timedelta(minutes=5)).run_id == "capped"
    assert store.latest(DAY, "r", NOW + timedelta(minutes=61)).run_id == "full"  # 만료되면 뺌
    assert store.latest(DAY, None, NOW + timedelta(minutes=61)).run_id == "full"
    store.close()


def test_make_concepts_saves_capped_ideas_briefly(tmp_path: Path, monkeypatch: Any) -> None:
    """상한으로 덜 만든 예시 아이디어(초안은 있음)는 짧게만 저장한다."""
    from ax_contracts.schemas import Evidence
    from ax_contracts.schemas.data import PromoConcept, PromoConceptsRequest
    from data_agent.skills import promo_concepts as promo

    async def generate(*args: Any, **kw: Any) -> promo.PromoResult:
        llm_failure.mark(LLMDailyCapReached("cap"))  # 두 번째 묻기·분류가 상한에 막힘
        out = PromoConceptsOutput(run_id="r1", generated_at=NOW, concepts=[
            PromoConcept(concept_id="c1", title="오늘의 한 권", borrowed_trend="")])  # fmt: skip
        return promo.PromoResult(out, Evidence(queries=[], data_summary="", collected_at=NOW))

    monkeypatch.setattr(promo, "_generate", generate)
    env = {promo.DB_ENV: str(tmp_path / "promo.db")}

    async def go() -> None:
        with llm_failure.scope():
            await promo.make_concepts(PromoConceptsRequest(), None, CappedLLM(), env=env,
                                      catalog=None, now=NOW)  # fmt: skip

    asyncio.run(go())
    store = PromoStore(tmp_path / "promo.db")
    rules = promo.rules_version(promo.load_config(env))
    assert store.latest(DAY, rules, NOW + timedelta(minutes=59)) is not None
    assert store.latest(DAY, rules, NOW + timedelta(minutes=60)) is None
    store.close()
