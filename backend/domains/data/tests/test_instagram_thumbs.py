"""인스타 레이더 썸네일: 필드 요청·릴스/사진 선택·호스트 허용·만료·예전 DB 마이그레이션.

실제 API 없음.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from data_agent import instagram_store as st
from data_agent.connectors import instagram_graph as ig
from data_agent.connectors.naver_searchad import JsonDailyCache
from data_agent.skills import instagram as igs

NOW = datetime(2026, 11, 18, 3, 0, tzinfo=UTC)
DAY = date(2026, 11, 18)
FUTURE = format(int((NOW + timedelta(days=4)).timestamp()), "x")  # oe: 16진수 유닉스 시각
PAST = format(int((NOW - timedelta(hours=1)).timestamp()), "x")
REEL_THUMB = f"https://scontent-ssn1-1.cdninstagram.com/v/t51/reel.jpg?stp=a&oe={FUTURE}"
PHOTO_URL = f"https://scontent.xx.fbcdn.net/v/t39/photo.jpg?oe={FUTURE}"


def test_business_discovery_requests_thumbnail_fields() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["fields"])
        return httpx.Response(200, json={"business_discovery": {"media": {"data": [
            {"id": "r", "media_type": "VIDEO", "media_product_type": "REELS",
             "thumbnail_url": REEL_THUMB},
            {"id": "p", "media_type": "IMAGE", "media_product_type": "FEED",
             "media_url": PHOTO_URL},
        ]}}})  # fmt: skip

    graph = ig.InstagramGraph({"DATA_IG_ACCESS_TOKEN": "t", "DATA_IG_USER_ID": "1"},
                              client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                              calls=JsonDailyCache())  # fmt: skip
    media = asyncio.run(graph.business_media("acct", 20))
    assert len(seen) == 1  # 필드를 더해도 호출은 1번
    for field in ("media_type", "media_url", "thumbnail_url"):
        assert field in seen[0]
    assert "children" not in seen[0]
    assert media[0]["thumbnail_url"] == REEL_THUMB and media[1]["media_url"] == PHOTO_URL


def test_thumb_prefers_thumbnail_url_then_media_url() -> None:
    assert st.thumb_of({"thumbnail_url": REEL_THUMB, "media_url": None}) == REEL_THUMB  # 릴스
    assert st.thumb_of({"media_url": PHOTO_URL}) == PHOTO_URL  # 사진·캐러셀 첫 장
    assert st.thumb_of({"thumbnail_url": REEL_THUMB, "media_url": PHOTO_URL}) == REEL_THUMB
    assert st.thumb_of({"thumbnail_url": "", "media_url": None}) is None
    assert st.thumb_of({}) is None


def test_safe_thumb_host_allowlist_and_https() -> None:
    assert igs.safe_thumb(REEL_THUMB, NOW) == REEL_THUMB
    assert igs.safe_thumb(PHOTO_URL, NOW) == PHOTO_URL
    for bad in (
        REEL_THUMB.replace("https://", "http://"),  # https만
        f"https://evil.example.com/a.jpg?oe={FUTURE}",  # 다른 호스트
        f"https://cdninstagram.com.evil.io/a.jpg?oe={FUTURE}",  # 끝이 다름
        f"https://notcdninstagram.com/a.jpg?oe={FUTURE}",  # 하위 도메인이 아님
        "javascript:alert(1)", "", None, 123,
    ):
        assert igs.safe_thumb(bad, NOW) is None, bad


def test_safe_thumb_expired_oe_is_none() -> None:
    assert igs.safe_thumb(f"https://a.cdninstagram.com/x.jpg?oe={PAST}", NOW) is None
    assert igs.safe_thumb("https://a.cdninstagram.com/x.jpg?oe=zz", NOW) is None  # 읽을 수 없음
    no_oe = "https://a.cdninstagram.com/x.jpg"
    assert igs.safe_thumb(no_oe, NOW) == no_oe  # 만료 표시가 없으면 그대로


def _media(account: str, thumb: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    out = []
    for i in range(4):
        at = (NOW - timedelta(days=1 if i == 0 else 10 + i)).isoformat()
        out.append({"id": f"{account}-{i}", "media_product_type": "REELS", "timestamp": at,
                    "view_count": 9000 if i == 0 else 1000, "permalink": f"https://ig/{i}",
                    **((thumb or {}) if i == 0 else {})})  # fmt: skip
    return out


def test_store_saves_thumb_and_bursts_carry_it(tmp_path: Path) -> None:
    path = tmp_path / "ig_radar.db"
    store = st.RadarStore(path)
    store.save(DAY, "a", _media("a", {"media_type": "VIDEO", "thumbnail_url": REEL_THUMB}))
    store.save(DAY, "b", _media("b", {"media_type": "IMAGE", "media_url": PHOTO_URL}))
    store.save(DAY, "c", _media("c", {"media_type": "VIDEO",
                                      "thumbnail_url": "https://evil.example.com/x.jpg"}))
    store.close()
    _, media, _ = st.radar_latest(path)
    posts = {p.account: p for p in igs.bursts(media, NOW, igs.IgConfig())}
    assert posts["a"].thumbnail_url == REEL_THUMB and posts["a"].media_type == "VIDEO"
    assert posts["b"].thumbnail_url == PHOTO_URL and posts["b"].media_type == "IMAGE"
    assert posts["c"].thumbnail_url is None  # 허용 목록 밖
    # 4일 뒤(만료 지남)에 다시 계산하면 None
    later = igs.bursts(media, NOW + timedelta(days=5), igs.IgConfig(burst_days=30))
    assert all(p.thumbnail_url is None for p in later)


OLD_SCHEMA = (
    "CREATE TABLE media (day TEXT NOT NULL, account TEXT NOT NULL,"
    " media_id TEXT NOT NULL, product_type TEXT, posted_at TEXT, like_count INTEGER,"
    " comments_count INTEGER, view_count INTEGER, caption TEXT, permalink TEXT,"
    " PRIMARY KEY (day, account, media_id));"
    "CREATE TABLE accounts (day TEXT NOT NULL, account TEXT NOT NULL,"
    " status TEXT NOT NULL, reason TEXT, PRIMARY KEY (day, account));"
)


def _old_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    for m in _media("old"):
        conn.execute("INSERT INTO media VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (DAY.isoformat(), "old", m["id"], "REELS", m["timestamp"], None, None,
                      m["view_count"], None, m["permalink"]))  # fmt: skip
    conn.execute("INSERT INTO accounts VALUES (?,?,?,?)", (DAY.isoformat(), "old", "ok", None))
    conn.commit()
    conn.close()


def test_old_db_is_read_before_migration_with_none_thumbs(tmp_path: Path) -> None:
    """수집기가 아직 열지 않은 예전 파일도 화면이 읽는다 (없는 열은 None)."""
    path = tmp_path / "ig_radar.db"
    _old_db(path)
    day, media, _ = st.radar_latest(path)
    assert day == DAY and len(media["old"]) == 4
    posts = igs.bursts(media, NOW, igs.IgConfig())
    assert posts and posts[0].thumbnail_url is None and posts[0].media_type is None


def test_old_db_migrates_columns_and_keeps_rows(tmp_path: Path) -> None:
    path = tmp_path / "ig_radar.db"
    _old_db(path)
    store = st.RadarStore(path)  # 열 때 ALTER TABLE ADD COLUMN
    store.save(DAY, "new", _media("new", {"media_type": "VIDEO", "thumbnail_url": REEL_THUMB}))
    store.close()
    st.RadarStore(path).close()  # 두 번 열어도 괜찮다 (이미 있으면 건너뜀)
    conn = sqlite3.connect(path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(media)")]
    conn.close()
    assert cols[-2:] == ["media_type", "thumb_url"]
    _, media, _ = st.radar_latest(path)
    posts = {p.account: p for p in igs.bursts(media, NOW, igs.IgConfig())}
    assert posts["old"].thumbnail_url is None  # 썸네일 없는 예전 행
    assert posts["new"].thumbnail_url == REEL_THUMB
