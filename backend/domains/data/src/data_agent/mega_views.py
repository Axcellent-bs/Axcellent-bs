"""조회 1,000만 이상 모음 (11/24). 저장분만 읽는다(새 호출 없음). 평소 대비 배수와 상관없이 넣는다.

인스타는 공식 API로 플랫폼 전체 인기 릴스를 뽑을 수 없다. 그래서 큰 계정을 레이더에 넣고 그 계정
릴스 중 조회 천만 이상을 모으고, 유튜브는 쇼츠 검색 결과(`meme_shorts`)에서 직접 거른다.

- 인스타: 레이더가 읽은 릴스(view_count 있음) 중 최근 30일 안 게시, `[instagram] radar_mega_views`
  (기본 10,000,000) 이상. 계정 층 태그(레이더 계정 메타)를 붙인다. 캡션은 넣지 않는다
- 유튜브: 쇼츠 검색 저장분 중 `[youtube] shorts_mega_views`(기본 10,000,000) 이상, 최근 30일 안
- 인스타 해시태그 top_media는 조회수가 없어 넣지 않는다
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ax_contracts.schemas.data import MegaViewItem, MegaViews, RadarAccountMeta

DAYS = 30
DEFAULT_MIN = 10_000_000
MAX_ITEMS = 30


def youtube_min(env: Mapping[str, str]) -> int:
    """[youtube] shorts_mega_views (기본 1,000만)."""
    from ax_agent_template.tenant import setting as tenant_setting

    path = env.get("DATA_TREND_CONFIG") or tenant_setting("DATA_TREND_CONFIG")
    if not path or not Path(path).is_file():
        return DEFAULT_MIN
    try:
        with Path(path).open("rb") as f:
            raw = tomllib.load(f).get("youtube") or {}
        return max(1, int(raw.get("shorts_mega_views", DEFAULT_MIN)))
    except (OSError, tomllib.TOMLDecodeError, TypeError, ValueError):
        return DEFAULT_MIN


def _at(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        t = datetime.fromisoformat(str(raw).replace("+0000", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def instagram_items(media: Mapping[str, Any], meta: Mapping[str, RadarAccountMeta],
                    minimum: int, now: datetime) -> list[MegaViewItem]:  # fmt: skip
    """레이더 미디어 중 릴스 조회가 기준 이상이고 30일 안인 것 (순수 계산)."""
    from data_agent.skills.instagram import safe_thumb

    since = now - timedelta(days=DAYS)
    out = []
    for account, items in media.items():
        tag = meta.get(account)
        for m in items:
            views = m.get("view_count")
            at = _at(m.get("timestamp"))
            if m.get("media_product_type") != "REELS" or not isinstance(views, int):
                continue
            if views < minimum or at is None or at < since:
                continue
            out.append(MegaViewItem(
                platform="instagram", account=account, item_id=str(m["id"]), views=views,
                posted_at=at, link=m.get("permalink"),
                thumbnail_url=safe_thumb(m.get("thumb_url"), now),
                account_kind=tag.kind if tag else None,
                audience_ages=list(tag.ages) if tag else [],
                audience_genders=list(tag.genders) if tag else [],
                topic=tag.topic if tag else None,
            ))  # fmt: skip
    return out


def youtube_items(videos: list[Mapping[str, Any]], minimum: int,
                  now: datetime) -> list[MegaViewItem]:  # fmt: skip
    """쇼츠 검색 저장분 중 조회가 기준 이상이고 30일 안인 것 (순수 계산)."""
    from data_agent.video_links import video_url

    since = now - timedelta(days=DAYS)
    out = []
    for v in videos:
        views, at = v.get("views"), _at(v.get("published_at"))
        if not isinstance(views, int) or views < minimum or (at is not None and at < since):
            continue
        out.append(MegaViewItem(
            platform="youtube", account=v.get("channel_title") or v.get("channel_id") or "?",
            item_id=v["video_id"], views=views, posted_at=at,
            link=video_url(v["video_id"], short=True), thumbnail_url=v.get("thumbnail_url"),
            title=v.get("title"),
        ))  # fmt: skip
    return out


def mega_views(env: Mapping[str, str], now: datetime | None = None) -> MegaViews:
    """레이더 최근 바퀴의 릴스 + 쇼츠 검색 마지막 저장분에서 조회 천만 이상 (조회수 순)."""
    from data_agent import company_scope, meme_shorts
    from data_agent import instagram_store as st
    from data_agent.radar_meta import load_meta
    from data_agent.skills.instagram import load_config

    now = now or datetime.now(UTC)
    config = load_config(env)
    out = MegaViews(instagram_min=config.mega_views, youtube_min=youtube_min(env), days=DAYS)
    _day, media, _failed = st.radar_latest(st.path_of(env, st.RADAR_ENV))
    meta = load_meta(config.radar_accounts, config.meme_accounts, company_scope.active())
    items = instagram_items(media, meta, out.instagram_min, now)
    path = meme_shorts.db_path(env)
    saved = None
    if str(path) == ":memory:" or Path(path).is_file():
        store = meme_shorts.Store(path)
        try:
            saved = store.latest()
        finally:
            store.close()
    if saved is not None:
        items += youtube_items(list(saved[1].get("videos") or []), out.youtube_min, now)
    if not media and saved is None:
        out.reason = "레이더 바퀴·쇼츠 검색 저장분이 아직 없어요"
        return out
    out.items = sorted(items, key=lambda i: (-i.views, i.item_id))[:MAX_ITEMS]
    out.status = "known"
    if not out.items:
        out.reason = f"최근 {DAYS}일 안에 조회 천만 이상인 릴스·쇼츠가 없어요"
    return out
