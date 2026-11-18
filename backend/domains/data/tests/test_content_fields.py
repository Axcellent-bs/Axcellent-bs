"""기획에 넘기는 영상 카테고리·형식 칸 (content_category·content_format). 가짜 값은 픽스처에만."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from ax_contracts.schemas.data import PromoConcept, TrendInsight, TrendInsightBriefOutput
from data_agent.content_fields import fill_concepts, fill_insights, for_concept, for_insight
from data_agent.skills import trend_insight_brief as brief
from data_agent.skills.shorts_stats import SHORTS_FORMATS, UNKNOWN
from data_agent.skills.trend_insight_brief import OTHER_FORMAT


def _concept(**kw: object) -> PromoConcept:
    return PromoConcept(concept_id="c1", title="t", borrowed_trend="책장 털기", **kw)  # type: ignore[arg-type]


def _insight(**kw: object) -> TrendInsight:
    return TrendInsight(insight_id="i1", rank=1, topic="책장 털기", line="한 줄", **kw)  # type: ignore[arg-type]


# ---------------------------------------------------------------- PromoConcept


def test_promo_shorts_format_15s_short() -> None:
    c = _concept(shorts_format="챌린지", duration_seconds=15, format="15초 숏폼")
    assert for_concept(c) == ("챌린지", "shorts")


def test_promo_long_form_is_none() -> None:
    assert for_concept(_concept(shorts_format="챌린지", format="롱폼")) == (None, None)


@pytest.mark.parametrize("name", [OTHER_FORMAT, UNKNOWN, None, "", "목록에 없는 형식"])
def test_promo_other_or_missing_is_none(name: str | None) -> None:
    assert for_concept(_concept(shorts_format=name, duration_seconds=15)) == (None, None)


def test_fill_concepts_sets_both_fields() -> None:
    items = [_concept(shorts_format="리액션"), _concept(shorts_format=OTHER_FORMAT)]
    fill_concepts(items)
    assert [(c.content_category, c.content_format) for c in items] == [
        ("리액션", "shorts"), (None, None)]  # fmt: skip


# ---------------------------------------------------------------- TrendInsight


def test_insight_topic_source() -> None:
    i = _insight(shorts_format="상황극·스케치", shorts_format_source="topic")
    assert for_insight(i) == ("상황극·스케치", "shorts")


@pytest.mark.parametrize("source", ["category", "all", None])
def test_insight_other_sources_are_none(source: str | None) -> None:
    insight = _insight(shorts_format="챌린지", shorts_format_source=source)
    assert for_insight(insight) == (None, None)


def test_insight_topic_but_other_format_is_none() -> None:
    assert for_insight(_insight(shorts_format=OTHER_FORMAT,
                                shorts_format_source="topic")) == (None, None)  # fmt: skip


def test_every_named_format_maps_to_itself() -> None:
    named = [f for f in SHORTS_FORMATS if f != OTHER_FORMAT]
    got = [for_insight(_insight(shorts_format=f, shorts_format_source="topic")) for f in named]
    assert got == [(f, "shorts") for f in named]


# ---------------------------------------------------------------- 예전 JSON


def test_old_json_without_new_fields_still_parses() -> None:
    old_concept = {"concept_id": "c", "title": "t", "borrowed_trend": "b",
                   "shorts_format": "챌린지"}  # fmt: skip
    c = PromoConcept.model_validate(old_concept)
    assert c.content_category is None and c.content_format is None
    old_insight = {"insight_id": "i", "rank": 1, "topic": "x", "line": "l",
                   "shorts_format": "챌린지", "shorts_format_source": "topic"}  # fmt: skip
    i = TrendInsight.model_validate(old_insight)
    assert i.content_category is None and i.content_format is None


def test_new_fields_validate() -> None:
    with pytest.raises(ValueError):
        _concept(content_format="tiktok")
    with pytest.raises(ValueError):
        _insight(content_category="")
    assert _insight(content_format="reels").content_format == "reels"


# ---------------------------------------------------------------- 저장된 예전 브리프 응답


def test_stored_old_brief_gets_fields_on_response_without_resaving(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """예전 브리프(새 칸 없이 저장)를 캐시로 응답할 때도 같은 규칙으로 채우고, 저장분은 그대로."""
    now = datetime(2026, 11, 18, 3, 0, tzinfo=UTC)
    env = {brief.DB_ENV: str(tmp_path / "brief.db")}
    rules = brief.rules_version(brief.load_config(env))
    old = TrendInsightBriefOutput.model_validate({
        "status": "known", "day": date(2026, 11, 18).isoformat(), "rules_version": rules,
        "insights": [
            {"insight_id": "i1", "rank": 1, "topic": "책장 털기", "line": "l",
             "shorts_format": "챌린지", "shorts_format_source": "topic"},
            {"insight_id": "i2", "rank": 2, "topic": "연말", "line": "l",
             "shorts_format": "리액션", "shorts_format_source": "all"},
        ],
    })  # fmt: skip
    store = brief.BriefStore(env[brief.DB_ENV])
    store.put(date(2026, 11, 18), rules, old, now)
    store.close()
    got = brief.cached_brief(brief.TrendInsightBriefRequest(), env, now)
    assert got is not None
    assert [(i.content_category, i.content_format) for i in got.output.insights] == [
        ("챌린지", "shorts"), (None, None)]  # fmt: skip
    store = brief.BriefStore(env[brief.DB_ENV])
    again = store.get(date(2026, 11, 18), rules, now)
    store.close()
    assert again is not None and again.insights[0].content_category is None  # 다시 저장하지 않음


def test_fill_insights_in_place() -> None:
    items = [_insight(shorts_format="정보·팁", shorts_format_source="topic")]
    fill_insights(items)
    assert (items[0].content_category, items[0].content_format) == ("정보·팁", "shorts")
