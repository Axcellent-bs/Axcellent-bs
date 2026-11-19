"""터짐 배수는 같은 지표끼리 (2026-11-19, 보고서 tr_4913cb6a11274238의 ×158.9).

예전에는 계정 중앙값을 "릴스 조회수 + 사진 좋아요"를 섞어 냈다. 사진 위주 계정의 릴스는
조회수를 좋아요 중앙값과 비교해 배수가 터졌다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from data_agent.skills import instagram as igs
from data_agent.skills.instagram_why import standout

NOW = datetime(2026, 11, 19, 3, 0, tzinfo=UTC)


def _photo(i: int, likes: int = 100, days: float = 1.0) -> dict[str, Any]:
    return {"id": f"p{i}", "media_product_type": "FEED", "media_type": "IMAGE",
            "timestamp": (NOW - timedelta(days=days + i)).isoformat(), "like_count": likes,
            "comments_count": 1, "caption": "사진"}  # fmt: skip


def _reel(i: int, views: int | None, likes: int = 50, days: float = 1.0) -> dict[str, Any]:
    return {"id": f"r{i}", "media_product_type": "REELS", "media_type": "VIDEO",
            "timestamp": (NOW - timedelta(days=days + i * 0.1)).isoformat(), "view_count": views,
            "like_count": likes, "comments_count": 1, "caption": "릴스"}  # fmt: skip


def _by_id(posts: list[Any]) -> dict[str, Any]:
    return {p.media_id: p for p in posts}


def test_reel_on_photo_account_compares_with_reel_views() -> None:
    # 사진 10개(좋아요 100) + 릴스 4개(조회 1000·1000·1000·3000)
    items = [_photo(i) for i in range(10)] + [_reel(i, 1000) for i in range(3)] + [_reel(3, 3000)]
    posts = _by_id(igs.bursts({"a": items}, NOW, igs.IgConfig()))
    p = posts["r3"]
    assert (p.metric, p.value, p.account_median, p.ratio) == ("view_count", 3000, 1000.0, 3.0)
    # 예전 계산이면 조회수 3000 ÷ 섞인 중앙값 100 = 30배였다
    assert set(posts) == {"r3"}


def test_reel_with_too_few_reel_views_uses_likes() -> None:
    # 릴스 조회수 값이 2개뿐 → 릴스도 좋아요끼리 (계정 좋아요 중앙값 100)
    items = [_photo(i) for i in range(10)] + [_reel(0, 50000, likes=300), _reel(1, 900, likes=40)]
    posts = _by_id(igs.bursts({"a": items}, NOW, igs.IgConfig()))
    p = posts["r0"]
    assert (p.metric, p.value, p.account_median, p.ratio) == ("like_count", 300, 100.0, 3.0)
    assert "r1" not in posts


def test_not_judged_when_both_metrics_have_under_three_values() -> None:
    items = [_photo(0, likes=10), _reel(0, 90000, likes=900)]
    assert igs.bursts({"a": items}, NOW, igs.IgConfig()) == []
    # 지표가 아예 없는 게시물은 판정하지 않는다
    base = {"view_count": 1.0, "like_count": 1.0}
    assert igs.judge({"media_product_type": "REELS"}, base) is None


def test_mixed_account_each_metric_against_its_own_median() -> None:
    # 릴스 5개(조회 2000, 하나는 6000) + 사진 5개(좋아요 200, 하나는 600). 좋아요 중앙값은 전체 10개
    items = [_reel(i, 2000, likes=100) for i in range(4)] + [_reel(4, 6000, likes=100)]
    items += [_photo(i, likes=200) for i in range(4)] + [_photo(4, likes=600)]
    posts = _by_id(igs.bursts({"a": items}, NOW, igs.IgConfig()))
    assert posts["r4"].metric == "view_count" and posts["r4"].ratio == 3.0
    base = igs.baselines(items)
    assert base == {"view_count": 2000.0, "like_count": 150.0}
    assert posts["p4"].metric == "like_count" and posts["p4"].ratio == 4.0
    assert posts["p4"].account_median == base["like_count"]


def test_standout_share_sentence_is_not_the_ratio_basis() -> None:
    # 사진 위주 계정의 릴스: standout은 "이 계정 평소 0%만 릴스" 같은 형식 문장일 수 있지만
    # 배수는 그 문장과 상관없이 같은 지표(좋아요)끼리 계산한다
    items = [_photo(i) for i in range(10)] + [_reel(0, None, likes=400)]
    post = _by_id(igs.bursts({"a": items}, NOW, igs.IgConfig()))["r0"]
    assert post.metric == "like_count" and post.ratio == 4.0 and post.account_median == 100.0
    line = standout(items[-1], items) or ""
    assert "배" not in line  # standout은 배수를 말하지 않는다
