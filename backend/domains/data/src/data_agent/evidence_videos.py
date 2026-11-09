"""근거 숏츠 칸 채우기: 제목·조회수(저장된 응답 값)와 영상 분류로 광고 재료 칸.

- 제목·채널·조회수: YouTube 스냅샷 저장분(`video_titles`·`video_details`·`snapshots`)과 하위 주제
  검색 저장분(`domain_subtopics`). 더 부르지 않는다. 없으면 None
- 광고 재료 칸(`ad_usable`·`caution_types`): 영상 분류(`classify_videos`, 저장 재사용)가 그 영상의
  실존 인물·상표 이름까지 판정했으면 그 결과로(`caution.judge`, 기준 "영상 분류"). 제목이 없거나
  분류를 못 했으면 그 카드 주제의 분류 결과(`fallback`)를 그대로 쓴다
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ax_contracts.schemas.data import VideoLink
from data_agent import caution
from data_agent.skills.topic_category import UNKNOWN, VideoInput, classify_videos


@dataclass(frozen=True)
class VideoInfo:
    title: str | None = None
    channel: str | None = None
    views: int | None = None


def stored_info(env: Mapping[str, str], now: datetime, ids: Sequence[str]) -> dict[str, VideoInfo]:
    """영상 id → 저장된 제목·채널·조회수 (YouTube 스냅샷이 먼저, 없으면 하위 주제 검색)."""
    from data_agent.skills.domain_subtopics import pool_shorts
    from data_agent.skills.shorts_stats import SHORTS_DB_ENV, SHORTS_DEFAULT_DB
    from data_agent.youtube_store import (
        DB_ENV,
        DEFAULT_DB,
        latest_views,
        video_details,
        video_titles,
    )

    wanted = list(dict.fromkeys(v for v in ids if v))
    if not wanted:
        return {}
    yt = env.get(DB_ENV) or DEFAULT_DB
    titles, details, views = (video_titles(yt, wanted), video_details(yt, wanted),
                              latest_views(yt, wanted))  # fmt: skip
    pool = {
        s.video_id: s
        for s in pool_shorts(env, now, env.get(SHORTS_DB_ENV) or SHORTS_DEFAULT_DB)
        if s.video_id in set(wanted)
    }
    out: dict[str, VideoInfo] = {}
    for vid in wanted:
        p = pool.get(vid)
        d = details.get(vid)
        out[vid] = VideoInfo(
            title=titles.get(vid) or (p.title if p else None) or None,
            channel=d.channel_title if d else None,
            views=views.get(vid, p.views if p else None),
        )
    return out


async def label(
    groups: Sequence[Sequence[VideoLink]],
    info: Mapping[str, VideoInfo],
    llm: Any,
    *,
    cache: Any = None,
    context_id: str | None = None,
) -> list[list[VideoLink]]:
    """카드마다 근거 영상 → 제목·조회수·광고 재료 칸을 채운 근거 영상 (같은 순서).

    영상 분류를 못 했거나 제목이 없는 영상은 이미 붙은 칸(카드 주제의 분류 결과)을 그대로 둔다.
    """
    ask = {
        v.video_id: VideoInput(v.video_id, i.title, i.channel)
        for links in groups
        for v in links
        if (i := info.get(v.video_id)) is not None and i.title
    }
    found = (
        await classify_videos(list(ask.values()), llm, cache=cache, context_id=context_id)
        if ask
        else {}
    )
    out: list[list[VideoLink]] = []
    for links in groups:
        filled: list[VideoLink] = []
        for v in links:
            i = info.get(v.video_id) or VideoInfo()
            r = found.get(v.video_id)
            if r is not None and r.category != UNKNOWN and r.real_entity is not None:
                warn = caution.judge(r.category, real_entity=r.real_entity, is_person=r.is_person,
                                     names=r.entity_names, basis="영상 분류")  # fmt: skip
                v = caution.apply(v, warn)
            elif r is not None and v.ad_usable is None:  # 영상 분류를 못 한 이유를 남긴다
                why = f"영상 분류 못 함 ({r.reason or '이유 없음'})"
                v = v.model_copy(update={"caution_reason": "; ".join(
                    x for x in (why, v.caution_reason) if x)})  # fmt: skip
            views = v.views if v.views is not None else i.views
            filled.append(v.model_copy(update={"title": v.title or i.title, "views": views}))
        out.append(filled)
    return out
