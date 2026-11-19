"""LLM 하루 상한에 닿아 덜 만든 결과.

- 예시 아이디어는 하루 캐시가 아니라 짧게만(`DATA_LLM_CAP_CACHE_MINUTES`, 기본 1시간) 저장한다
- 브리프 자동 판(11/19~)은 한국 날짜당 한 번만 새로 만든다. 덜 만든 판은 읽기만 하다가 예산이
  풀리면 그날 한 번만 다시(auto_retry). LLM이 막혔으면 재료를 계산하지 않고 이전 판 + 안내

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


# 11/19: 예전에는 상한 판을 60분만 저장해 1시간마다 미리 계산이 네이버·LLM을 다시 불렀다(하루 13판).
# 이제 자동 판은 한국 날짜당 한 번만 새로 만들고, 덜 만든 판이면 예산이 풀렸을 때 그날 한 번만 다시.


class CountingAges:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, keywords: list[str]) -> TrendAgeOutput:
        self.calls += 1  # 네이버(trend_age) 호출 자리
        return TrendAgeOutput(status="known", items=[])


def _brief_with(tmp_path: Path, llm: Any, now: datetime, ages: Any) -> Any:
    env = {brief.DB_ENV: str(tmp_path / "brief.db")}

    async def go():  # noqa: ANN202
        with llm_failure.scope():
            return await brief.make_brief(
                TrendInsightBriefRequest(), llm, env=env, popular=POPULAR, popular_reason=None,
                rising=[], age_fn=ages, promo=None, catalog=None, now=now,
            )  # fmt: skip

    return asyncio.run(go()).output


def _autos(tmp_path: Path) -> list[str]:
    store = brief.BriefStore(tmp_path / "brief.db")
    rules = brief.rules_version(brief.load_config({}))
    try:
        return [v["trigger"] for v in store.auto_versions(DAY, rules)]
    finally:
        store.close()


def _budget(monkeypatch: pytest.MonkeyPatch, full: bool) -> None:
    b = llm_budget.Budget()
    if full:
        b.add(10**6, 10**9)
    monkeypatch.setattr(llm_budget, "BUDGET", b)


def test_capped_brief_is_read_only_while_capped_then_retried_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _budget(monkeypatch, full=False)
    ages, llm = CountingAges(), CappedLLM()
    first = _brief_with(tmp_path, llm, NOW, ages)  # 만드는 중에 상한 → 덜 만든 판
    assert any("LLM 하루 상한에 닿아 덜 만든 브리프" in x for x in first.limits)
    assert ages.calls == 1 and _autos(tmp_path) == ["auto"]
    # 상한 상태: 1시간 뒤·5시간 뒤 미리 계산도 네이버·LLM을 부르지 않고 저장분만
    _budget(monkeypatch, full=True)
    asked = llm.asked
    for hours in (1, 5):
        again = _brief_with(tmp_path, llm, NOW + timedelta(hours=hours), ages)
        assert again.cached
    assert ages.calls == 1 and llm.asked == asked and _autos(tmp_path) == ["auto"]
    # 예산이 풀리면 그날 한 번만 다시(auto_retry)
    _budget(monkeypatch, full=False)
    fresh = CappedLLM(capped=False)
    retried = _brief_with(tmp_path, fresh, NOW + timedelta(hours=6), ages)
    assert not retried.cached and fresh.asked > 0 and ages.calls == 2
    assert _autos(tmp_path) == ["auto", "auto_retry"]
    # 그 뒤로는 하루 내내 읽기만 (다시 상한으로 덜 만들어졌어도)
    for hours in (7, 12):
        assert _brief_with(tmp_path, CappedLLM(), NOW + timedelta(hours=hours), ages).cached
    assert ages.calls == 2 and _autos(tmp_path) == ["auto", "auto_retry"]


def test_failed_brief_is_saved_and_not_rebuilt_every_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Broken(CappedLLM):
        async def structured(self, *args: Any, **kwargs: Any) -> Any:
            self.asked += 1
            error = RuntimeError("공급자 오류")
            llm_failure.mark(error)
            raise error

    _budget(monkeypatch, full=False)
    ages = CountingAges()
    first = _brief_with(tmp_path, Broken(), NOW, ages)
    assert any("한 번만 다시 만든다" in x for x in first.limits)
    # 예산이 충분하니 다음 회차에 한 번만 다시, 또 실패해도 그 뒤는 읽기만
    for hours in (1, 2, 3, 4):
        _brief_with(tmp_path, Broken(), NOW + timedelta(hours=hours), ages)
    assert ages.calls == 2 and _autos(tmp_path) == ["auto", "auto_retry"]


def test_llm_blocked_stops_before_any_material(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _budget(monkeypatch, full=True)
    ages, llm = CountingAges(), CappedLLM()
    out = _brief_with(tmp_path, llm, NOW, ages)  # 그날 판도 없고 상한
    assert brief.CAPPED_NOTE in out.limits or out.reason == brief.CAPPED_NOTE
    assert ages.calls == 0 and llm.asked == 0 and _autos(tmp_path) == []
    # 핸들러가 재료를 계산하기 전에 보는 cached_brief도 같은 판을 준다 (숏츠·네이버 안 부름)
    env = {brief.DB_ENV: str(tmp_path / "brief.db")}
    hit = brief.cached_brief(TrendInsightBriefRequest(), env, NOW)
    assert hit is not None and hit.output.reason == brief.CAPPED_NOTE


def test_blocked_shows_the_previous_day_brief_with_the_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _budget(monkeypatch, full=False)
    _brief_with(tmp_path, CappedLLM(capped=False), NOW - timedelta(days=1), CountingAges())
    _budget(monkeypatch, full=True)
    ages = CountingAges()
    out = _brief_with(tmp_path, CappedLLM(), NOW, ages)
    assert out.cached and out.day == DAY - timedelta(days=1) and brief.CAPPED_NOTE in out.limits
    assert ages.calls == 0


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
