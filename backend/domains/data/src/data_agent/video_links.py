"""영상 주소·썸네일 (`VideoLink`). 썸네일은 이미 받은 YouTube 응답에서 저장한 값만 쓴다.

썸네일을 얻으려고 YouTube를 더 부르지 않는다. 저장된 값이 없으면 `thumbnail_url`은 None.
영상 주소는 id로 만든다 (호출 아님).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from ax_contracts.schemas.data import VideoLink
from data_agent.youtube_store import video_thumbnails


def video_url(video_id: str, *, short: bool = False) -> str:
    if short:
        return f"https://www.youtube.com/shorts/{video_id}"
    return f"https://www.youtube.com/watch?v={video_id}"


def links(
    video_ids: Iterable[str], thumbs: Mapping[str, str], *, short: bool = False
) -> list[VideoLink]:
    return [
        VideoLink(video_id=v, video_url=video_url(v, short=short), thumbnail_url=thumbs.get(v))
        for v in video_ids
        if v
    ]


def thumbs_of(db: str | Path | None, video_ids: Iterable[str]) -> dict[str, str]:
    """저장된 썸네일 (db가 없거나 표가 없으면 빈 값)."""
    ids = [v for v in dict.fromkeys(video_ids) if v]
    if db is None or not ids:
        return {}
    return video_thumbnails(db, ids)


def links_from(
    db: str | Path | None, video_ids: Iterable[str], *, short: bool = False
) -> list[VideoLink]:
    ids = [v for v in video_ids if v]
    return links(ids, thumbs_of(db, ids), short=short)
