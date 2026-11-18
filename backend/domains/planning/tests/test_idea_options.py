"""기획 방향 선택지 계산 (11/18): 갈래·후보·추천은 코드 규칙, 같은 입력이면 같은 결과."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from ax_contracts.schemas.data import TrendInsight
from ax_contracts.schemas.marketing_channels import ChannelFollowersOutput
from ax_contracts.schemas.planning import InsightPast, ReceiveInsightInput
from ax_workflow.models import ScaleFacts
from planning_agent import idea_options as io

pytestmark = pytest.mark.role("planning")


def _inp(**kw: Any) -> ReceiveInsightInput:
    base = {
        "insight_id": "ins-self", "title": "챌린지", "summary": "챌린지 숏츠가 많이 보인다",
        "skill": "trend_insight_brief",
        "facts": [{"label": "중앙값", "value": "506만", "source": "s"}],
        "products": ["밤의 도서관"], "shorts_format": "챌린지",
    }  # fmt: skip
    return ReceiveInsightInput.model_validate({**base, **kw})


def _t(
    topic: str,
    *,
    fmt: str = "챌린지",
    ad: bool | None = True,
    real: bool | None = False,
    score: float = 1.0,
    iid: str | None = None,
    views: float | None = 50000.0,
    topic_views: float | None = 48000.0,
) -> Any:
    """데이터분석 TrendInsight + 11/18 재현 님이 더하는 주제 자기 숏츠 칸(아직 계약에 없음)."""
    t = TrendInsight(
        insight_id=iid or f"t-{topic}", rank=1, topic=topic, line="l", shorts_format=fmt,
        ad_material=ad, real_entity=real, score=score, shorts_format_median_views=views,
    )  # fmt: skip
    extra = {
        "shorts_format_topic_median_views": topic_views,
        "shorts_format_topic_at": None,
        "shorts_format_topic_examples": [SimpleNamespace(title=f"{topic} 영상", views=1)],
    }
    return SimpleNamespace(**t.model_dump(), **extra)


def _opt(opts, angle):
    return next(o for o in opts if o.id == angle)


def test_candidates_filter_format_ad_entity_self() -> None:
    trends = [
        _t("낮음", score=1.0), _t("높음", score=5.0), _t("다른형식", fmt="브이로그"),
        _t("분류전", ad=None), _t("실존", real=True), _t("자기", iid="ins-self"), _t("챌린지"),
    ]  # fmt: skip
    refs = io.trend_candidates(_inp(), trends)
    assert [r.topic for r in refs] == ["높음", "낮음"]
    assert refs[0].median_views == 48000.0  # 형식 전체가 아니라 그 주제 자기 숏츠 중앙값
    assert refs[0].examples == ["높음 영상"]


def test_candidates_need_topic_own_numbers() -> None:
    """주제 자기 숏츠 중앙값이 없으면(형식이 카테고리·전체에서 채워진 주제) 후보가 아니다."""
    assert io.trend_candidates(_inp(), [_t("빌린형식", topic_views=None)]) == []
    plain = TrendInsight(insight_id="p", rank=1, topic="예전응답", line="l", shorts_format="챌린지",
                         ad_material=True, real_entity=False)  # fmt: skip
    assert io.trend_candidates(_inp(), [plain]) == []


def test_candidates_at_most_three_and_none_without_format() -> None:
    trends = [_t(f"x{i}", score=float(i)) for i in range(5)]
    assert len(io.trend_candidates(_inp(), trends)) == 3
    assert io.trend_candidates(_inp(shorts_format=None), trends) == []


def test_small_scale_recommends_ride_trend() -> None:
    scale = ScaleFacts(followers=300, followers_channel="instagram")
    opts = io.build_options(_inp(), [_t("유행 챌린지")], scale)
    assert [o.id for o in opts] == ["ride_trend", "own_trend", "with_product"]
    ride = _opt(opts, "ride_trend")
    assert ride.recommended and "300" in (ride.reason or "")
    assert sum(o.recommended for o in opts) == 1


def test_one_number_is_enough() -> None:
    scale = ScaleFacts(past_views_median=50000.0, past_views_count=2)
    opts = io.build_options(_inp(), [_t("유행 챌린지")], scale)
    assert _opt(opts, "with_product").recommended
    assert not _opt(opts, "ride_trend").recommended


def test_own_trend_when_big_and_no_products() -> None:
    scale = ScaleFacts(followers=50000, followers_channel="youtube")
    opts = io.build_options(_inp(products=[]), [_t("유행 챌린지")], scale)
    assert _opt(opts, "own_trend").recommended


def test_no_scale_numbers_no_recommendation() -> None:
    opts = io.build_options(_inp(), [_t("유행 챌린지")], ScaleFacts())
    assert not any(o.recommended for o in opts)
    assert _opt(opts, "own_trend").reason == "규모 숫자를 받지 못해 추천 없음"


def test_blocked_reasons() -> None:
    s = ScaleFacts(followers=300)
    no_fmt = _opt(io.build_options(_inp(shorts_format=None), [_t("a")], s), "ride_trend")
    assert not no_fmt.available and no_fmt.blocked_reason == "형식 정보 없음"
    no_list = _opt(io.build_options(_inp(), None, s), "ride_trend")
    assert no_list.blocked_reason == "트렌드 목록을 받지 못함"
    none = _opt(io.build_options(_inp(), [_t("a", fmt="브이로그")], s), "ride_trend")
    assert none.blocked_reason == "지금 뜨는 후보 없음"
    prod = _opt(io.build_options(_inp(products=[]), [], s), "with_product")
    assert not prod.available and prod.blocked_reason == "연결된 우리 상품 없음"
    assert _opt(io.build_options(_inp(), None, s), "own_trend").available


def test_same_input_same_output() -> None:
    args = (_inp(), [_t("a"), _t("b", score=2)], ScaleFacts(followers=300))
    first = [o.model_dump() for o in io.build_options(*args)]
    assert first == [o.model_dump() for o in io.build_options(*args)]


def test_followers_use_largest_channel() -> None:
    out = ChannelFollowersOutput.model_validate(
        {"status": "known", "channels": [
            {"channel": "instagram", "followers": 300}, {"channel": "youtube", "followers": 1200}]}
    )  # fmt: skip
    s = io.scale_facts([], out)
    assert (s.followers, s.followers_channel) == (1200, "youtube")
    assert io.scale_facts([], ChannelFollowersOutput(status="unknown")).followers is None
    assert io.scale_facts([], None).followers is None


def test_past_median_ignores_missing_views() -> None:
    past = [InsightPast(campaign_id="a", views=100), InsightPast(campaign_id="b"),
            InsightPast(campaign_id="c", views=300)]  # fmt: skip
    s = io.scale_facts(past, None)
    assert s.past_views_median == 200 and s.past_views_count == 2


def test_thresholds_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    assert io.small_followers() == 10000 and io.small_views() == 10000
    monkeypatch.setenv("PLANNING_SMALL_FOLLOWERS", "100")
    scale = ScaleFacts(followers=300, followers_channel="instagram")
    assert not _opt(io.build_options(_inp(), [_t("a")], scale), "ride_trend").recommended
