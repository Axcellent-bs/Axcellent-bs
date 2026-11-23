"""회사 유튜브에 올린 영상의 성과(조회수·좋아요·댓글)와 올린 목록 (read, 2026-11-23).

데이터분석이 캠페인·의뢰 결과를 기획에 돌려줄 때 읽는다
(`youtube_video_stats`·`youtube_uploads_list`).

- 회사 토큰(`youtube._secret`, 로그인한 회사의 채널 연결)으로 `videos.list?part=statistics`를 읽는다
- 영상마다 `STATS_TTL`초 캐시한다(회사별). 데이터·화면이 자주 물어도 유튜브 할당량을 아낀다
- 숫자를 지어내지 않는다: 숨긴 좋아요·댓글은 `None`(모름), 못 찾은 영상은 `not_found`
- 키·토큰 값은 응답·로그에 넣지 않는다(실패는 사유 코드만)
"""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from ax_contracts.schemas.marketing_youtube_stats import (
    YOUTUBE_STATS_MAX,
    YOUTUBE_VIDEO_ID_PATTERN,
    YoutubeUpload,
    YoutubeVideoStat,
)
from marketing_agent import creative_records as _creative
from marketing_agent import youtube as _youtube

logger = logging.getLogger(__name__)

STATS_URL = "https://www.googleapis.com/youtube/v3/videos"
STATS_TTL = 300.0  # 5분
LINK_KEYS = ("campaign_id", "request_id")  # 기록·render.youtube에 남은 캠페인(cmp_)·의뢰(wr_) 번호

# (회사, video_id) → (읽은 monotonic 시각, 값)
_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _company() -> str:
    from ax_agent_template import company_ai

    return company_ai.company_id() or ""


def _count(raw: Any) -> int | None:
    """유튜브 statistics 값(문자열 숫자). 없으면 None(모름) — 0으로 채우지 않는다."""
    if raw is None:
        return None
    try:
        value = int(str(raw))
    except ValueError:
        return None
    return value if value >= 0 else None


def _link_of(rec: dict[str, Any], yt: dict[str, Any]) -> str | None:
    for source in (yt, rec):
        for key in LINK_KEYS:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:80]
    return None


def uploads(limit: int = 50, campaign_id: str | None = None) -> list[YoutubeUpload]:
    """올리기가 끝난(`render.youtube.status == done`) 기록. 최근 기록부터."""
    store = _creative.store()
    out: list[YoutubeUpload] = []
    for row in store.list(_creative.KEEP):
        if (row.get("render") or {}).get("youtube") != "done":
            continue
        rec = store.get(row["record_id"]) or {}
        yt = dict((rec.get("render") or {}).get("youtube") or {})
        video_id = str(yt.get("video_id") or "")
        if not re.fullmatch(YOUTUBE_VIDEO_ID_PATTERN, video_id):
            continue
        link = _link_of(rec, yt)
        if campaign_id and link != campaign_id:
            continue
        out.append(
            YoutubeUpload(
                record_id=row["record_id"],
                video_id=video_id,
                url=yt.get("url") or f"https://youtu.be/{video_id}",
                uploaded_at=yt.get("uploaded_at"),
                title=yt.get("title"),
                privacy=yt.get("privacy"),
                campaign_id=link,
            )
        )
        if len(out) >= limit:
            break
    return out


def _resolve(record_ids: list[str]) -> tuple[dict[str, str], list[str]]:
    """기록 id → video_id. 기록이 없거나 아직 올리지 않았으면 missing."""
    found: dict[str, str] = {}
    missing: list[str] = []
    store = _creative.store()
    for rid in dict.fromkeys(record_ids):
        yt = (store.get_render(rid) or {}).get("youtube") or {}
        vid = str(yt.get("video_id") or "")
        if yt.get("status") == "done" and re.fullmatch(YOUTUBE_VIDEO_ID_PATTERN, vid):
            found[vid] = rid
        else:
            missing.append(rid)
    return found, missing


async def _fetch(ids: list[str]) -> dict[str, dict[str, Any]]:
    """유튜브에서 읽는다. 돌려준 id만 키로(없는 id는 not_found)."""
    got: dict[str, dict[str, Any]] = {}
    async with _youtube.CLIENT() as client:
        token = await _youtube._access_token(client)
        for i in range(0, len(ids), YOUTUBE_STATS_MAX):
            res = await client.get(
                STATS_URL,
                params={"part": "statistics", "id": ",".join(ids[i : i + YOUTUBE_STATS_MAX])},
                headers={"Authorization": f"Bearer {token}"},
            )
            if res.status_code >= 400:
                raise _youtube.YoutubeError(f"stats_http_{res.status_code}")
            for item in res.json().get("items") or []:
                stats = item.get("statistics") or {}
                got[str(item.get("id"))] = {
                    "views": _count(stats.get("viewCount")),
                    "likes": _count(stats.get("likeCount")),
                    "comments": _count(stats.get("commentCount")),
                }
    return got


def _stat(vid: str, rid: str | None, value: dict[str, Any] | None, at: str) -> YoutubeVideoStat:
    if value is None:
        return YoutubeVideoStat(video_id=vid, record_id=rid, status="not_found", measured_at=at)
    return YoutubeVideoStat(
        video_id=vid,
        record_id=rid,
        status="ok",
        url=f"https://youtu.be/{vid}",
        measured_at=at,
        **value,
    )


async def video_stats(video_ids: list[str], record_ids: list[str]) -> dict[str, Any]:
    """`YoutubeVideoStatsOutput` 모양 dict. 캐시에 있는 영상은 다시 읽지 않는다."""
    checked_at = _now()
    by_record, missing = _resolve(record_ids)
    ids = list(dict.fromkeys([*video_ids, *by_record]))
    base: dict[str, Any] = {"missing_records": missing, "checked_at": checked_at}
    if not ids:
        return {**base, "status": "unknown", "reason": "no_videos", "items": []}
    company = _company()
    fresh: dict[str, tuple[dict[str, Any] | None, str]] = {}
    for vid in ids:
        hit = _cache.get((company, vid))
        if hit and time.monotonic() - hit[0] < STATS_TTL:
            fresh[vid] = (hit[1].get("value"), hit[1]["at"])
    need = [v for v in ids if v not in fresh]
    if need:
        if not _youtube.configured():
            return {**base, "status": "unknown", "reason": "missing_keys", "items": []}
        try:
            got = await _fetch(need)
        except _youtube.YoutubeError as error:
            return {**base, "status": "unknown", "reason": str(error), "items": []}
        except (httpx.HTTPError, ValueError) as error:
            logger.warning("youtube stats 실패: %s", type(error).__name__)
            return {**base, "status": "unknown", "reason": "error", "items": []}
        for vid in need:
            value = got.get(vid)
            _cache[(company, vid)] = (time.monotonic(), {"value": value, "at": checked_at})
            fresh[vid] = (value, checked_at)
    items = [_stat(v, by_record.get(v), *fresh[v]).model_dump(mode="json") for v in ids]
    return {**base, "status": "ok", "reason": None, "items": items}


def clear_cache() -> None:
    """테스트용."""
    _cache.clear()
