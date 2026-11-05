"""v2 화면 칸 채우기: 영상 광고 재료 여부·확인할 것, 인사이트 숏츠 적합도·목표 참고값.

규칙은 모두 코드다 (LLM을 더 부르지 않는다). 모르면 None + 이유.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ax_contracts.schemas.data import PopularVideo, ShortsGroup, ShortsStatsOutput, VideoLink
from data_agent import caution
from data_agent.skills.trend_insight_brief import insight_shorts_fit


def test_topic_classification_decides_the_cautions() -> None:
    person = caution.judge("연예·스포츠", real_entity=True, is_person=True, names=["손흥민"])
    assert person.ad_usable is False and person.types == ["real_person"]
    assert "손흥민" in (person.reason or "")
    brand = caution.judge("게임", real_entity=True, is_person=False, names=["리그오브레전드"])
    assert brand.ad_usable is False and brand.types == ["trademark"]
    song = caution.judge("K-pop·음악", real_entity=False, is_person=False)
    assert song.ad_usable is False and song.types == ["music"]
    plain = caution.judge("IT", real_entity=False, is_person=False)
    assert plain.ad_usable is True and plain.types == [] and plain.reason is None


def test_unknown_classification_is_none_with_a_reason() -> None:
    pending = caution.judge(None, real_entity=None, is_person=None, basis="주제 'x' 분류")
    assert pending.ad_usable is None and "분류를 몰라" in (pending.reason or "")
    no_names = caution.judge("IT", real_entity=None, is_person=None)
    assert no_names.ad_usable is None and "실존 인물·상표 판단이 없음" in (no_names.reason or "")


def test_top_videos_use_the_title_and_never_claim_usable_without_names() -> None:
    topics = [(["손흥민"], True), (["리그오브레전드"], False)]
    hit = caution.for_title("손흥민 리그오브레전드 하이라이트", "게임", topics)
    assert hit.ad_usable is False and hit.types == ["real_person", "trademark"]
    song = caution.for_title("새 앨범 뮤직비디오", "K-pop·음악", topics)
    assert song.ad_usable is False and song.types == ["music"]
    other = caution.for_title("코딩 꿀팁", "IT", topics)
    assert other.ad_usable is None and other.types == []  # 이름을 안 봤으니 true라고 하지 않는다
    video = caution.apply(PopularVideo(video_id="v1", title="x"), hit)
    link = caution.apply(VideoLink(video_id="v1"), song)
    assert video.caution_types == ["real_person", "trademark"] and link.ad_usable is False


def _shorts(count: int) -> ShortsStatsOutput:
    return ShortsStatsOutput(
        status="known", snapshot_at=datetime(2026, 11, 5, tzinfo=UTC), median_views=10000.0,
        by_category=[ShortsGroup(name="게임", count=count, median_views=14000.0)],
    )  # fmt: skip


def test_insight_shorts_fit_needs_five_samples() -> None:
    fit, status, why, target, basis = insight_shorts_fit("게임", _shorts(8))
    assert status == "known" and fit is not None and fit.samples == 8 and fit.ratio == 1.4
    assert target == 14000.0 and basis
    fit, status, why, target, _ = insight_shorts_fit("게임", _shorts(3))
    assert status == "unknown" and fit is None and target is None
    assert "표본 3개 (최소 5개)" in (why or "")
    none = insight_shorts_fit("게임", None)
    assert none[0] is None and none[1] == "unknown"
