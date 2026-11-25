"""브랜드 공지·광고 게시물 분류 (11/26 C). 코드 규칙, LLM·인스타 호출 없음.

증거: 레이더 "갑자기 뜬 게시물" 12개 중 밈 계정 건 1개, 나머지는 방송 예고·티켓 공지 같은 브랜드
게시물. notice_ad는 보고서 근거·밈 재료에서 빠지고(excluded notice_ad), 레이더 화면에는 칩만.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ax_contracts.schemas.data import InstagramRadarPost, TrendReport
from data_agent.skills import notice_ad as na
from data_agent.skills import trend_report as tr
from data_agent.skills import trend_report_topic as tt
from data_agent.skills.instagram import meme_posts

FIXTURE = Path(__file__).parent / "fixtures" / "trend_report_example.json"
NOW = datetime(2026, 11, 25, 12, tzinfo=UTC)


def test_notice_captions_are_notice_ad() -> None:
    # 브랜드(기업 계정)는 공지 낱말 1개 이상
    assert na.classify("12/3 콘서트 티켓 예매 오픈! 선착순") is not None
    assert na.classify("오늘 밤 9시 첫 방송, 놓치지 마세요") is not None
    assert na.classify("신제품 출시 기념 할인 이벤트", account_kind="brand") is not None
    assert na.classify("평범한 캡션", "AD") == "유료 광고 표시"  # 공식 API 표시
    assert na.classify("너무 좋다 #광고") == "광고 표시 #광고"
    assert na.classify("후기 남겨요 #ad", source_kind="meme") == "광고 표시 #ad"
    assert na.classify("유료 광고 포함 · 써 보니", source_kind="meme") is not None


def test_meme_captions_are_not_notice_ad() -> None:
    assert na.classify("시험 전날 계획표 vs 새벽 3시의 나 ㅋㅋ", source_kind="meme") is None
    assert na.classify("공부 계획만 세 번째 새로 씀", account_kind="meme") is None
    # 밈 계정은 공지 낱말 하나로는 아님 (2개 이상이어야)
    assert na.classify("가게 오픈 시간에 맞춰 간 나", source_kind="meme") is None
    # 해시태그는 낱말이 끝까지 같아야 (#adventure·#광고천재는 광고 표시가 아님)
    assert na.classify("주말 #adventure #광고천재", source_kind="meme") is None
    assert na.classify(None) is None


def _media(caption: str, views: int, at: datetime, mid: str) -> dict:
    return {"id": mid, "media_product_type": "REELS", "timestamp": at.isoformat(),
            "view_count": views, "like_count": 10, "comments_count": 1, "caption": caption,
            "media_type": "VIDEO"}  # fmt: skip


def test_bursts_mark_notice_ad_and_meme_posts_skip_them() -> None:
    from data_agent.skills.instagram import bursts, load_config

    config = load_config({})
    old = [_media("평소 게시물", 1000, NOW - timedelta(days=20 + i), f"o{i}") for i in range(8)]
    media = {"brandco": [*old, _media("쿠팡플레이 콘서트 티켓 예매 공지", 90000, NOW, "b1")],
             "memeacc": [*old, _media("시험 전날의 나 ㅋㅋ", 90000, NOW, "m1")]}  # fmt: skip
    posts = {p.media_id: p for p in bursts(media, NOW, config)}
    assert posts["b1"].notice_ad and "티켓" in posts["b1"].notice_ad
    meme = posts["m1"].model_copy(update={"source_kind": "meme"})
    ad = posts["b1"].model_copy(update={"source_kind": "meme"})
    assert posts["m1"].notice_ad is None
    assert [p.media_id for p in meme_posts([meme, ad])] == ["m1"]  # 밈 재료에서 빠짐


def test_report_evidence_drops_notice_ad_even_without_theme() -> None:
    r = TrendReport.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))
    assert r.brief.theme is None
    post = r.sections.content.posts[0]
    post.caption_excerpt, post.notice_ad = "현대카드 캐러셀 · 연말 혜택 이벤트", "공지 낱말 이벤트"
    assert "content:1" in tr.evidence_catalog(r)
    tt.apply(r, None)
    assert [(e.evidence_id, e.reason) for e in r.excluded] == [("content:1", "notice_ad")]
    assert "content:1" not in tr.evidence_catalog(r)
    assert r.sections.content.posts[0].notice_ad  # 원자료 표에는 그대로 남는다


def test_old_stored_post_without_field_is_checked_by_rule() -> None:
    old = InstagramRadarPost.model_validate({
        "account": "brandco", "media_id": "1", "metric": "view_count", "value": 10,
        "account_median": 1.0, "ratio": 10.0, "caption_excerpt": "사전예약 오픈 안내"})
    assert old.notice_ad is None and na.is_notice_ad(old)  # 기업 계정(benchmark) + 공지 낱말
    meme_old = old.model_copy(update={"source_kind": "meme"})
    assert not na.is_notice_ad(meme_old.model_copy(update={"caption_excerpt": "오픈런 실패한 나"}))
