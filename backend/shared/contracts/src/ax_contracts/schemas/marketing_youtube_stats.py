"""회사 유튜브에 올린 영상의 성과 읽기 (read, 2026-11-23).

마케팅이 제작 기록 영상을 회사 유튜브 채널에 올리면(`creative_youtube_request`) 기록의
`render.youtube`에 video_id·주소가 남는다. 데이터분석이 그 영상들의 성과를 읽어 기획에 돌려준다.

- `youtube_uploads_list`: 올린 영상 목록(기록 id·video_id·주소·올린 시각·제목·연결된 캠페인)
- `youtube_video_stats`: 회사 토큰으로 YouTube Data API `videos.list?part=statistics`를 읽어
  조회수·좋아요·댓글 수를 준다. 짧게 캐시한다
- **`None` = 모름, 0 = 0**. 채널이 좋아요·댓글 수를 숨기면 그 칸은 `None`이다
- 키·토큰 값은 응답에 넣지 않는다
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, model_validator

from ax_contracts.schemas.common import AxModel
from ax_contracts.schemas.marketing_creative import RECORD_ID_PATTERN

YOUTUBE_VIDEO_STATS_SKILL = "youtube_video_stats"
YOUTUBE_UPLOADS_LIST_SKILL = "youtube_uploads_list"
YOUTUBE_VIDEO_ID_PATTERN = r"^[A-Za-z0-9_-]{11}$"
YOUTUBE_STATS_MAX = 50  # videos.list가 한 번에 받는 id 수


class YoutubeVideoStatsInput(AxModel):
    """video_id 목록이나 제작 기록 id 목록(둘 다 줘도 된다). 하나는 있어야 한다."""

    video_ids: list[str] = Field(default_factory=list, max_length=YOUTUBE_STATS_MAX)
    record_ids: list[str] = Field(default_factory=list, max_length=YOUTUBE_STATS_MAX)

    @model_validator(mode="after")
    def _ids(self) -> YoutubeVideoStatsInput:
        if not self.video_ids and not self.record_ids:
            raise ValueError("video_ids나 record_ids 중 하나는 있어야 한다")
        bad = [v for v in self.video_ids if not re.fullmatch(YOUTUBE_VIDEO_ID_PATTERN, v)]
        bad += [r for r in self.record_ids if not re.fullmatch(RECORD_ID_PATTERN, r)]
        if bad:
            raise ValueError(f"id 모양이 맞지 않음: {len(bad)}개")
        return self


class YoutubeVideoStat(AxModel):
    """영상 하나. `not_found`면 지웠거나 비공개로 바꿔 읽을 수 없다(숫자 칸은 비운다)."""

    video_id: str
    record_id: str | None = None  # 제작 기록 id로 물었으면 그 기록
    status: Literal["ok", "not_found"]
    views: int | None = None
    likes: int | None = None  # 채널이 숨기면 None
    comments: int | None = None  # 댓글을 막으면 None
    url: str | None = None
    measured_at: str | None = None  # 유튜브에서 읽은 시각(캐시면 그때 시각)


class YoutubeVideoStatsOutput(AxModel):
    """`unknown`이면 `reason`: no_videos · missing_keys · invalid_credentials · stats_http_<코드>
    · error. 기록이 없거나 아직 올리지 않은 기록 id는 `missing_records`에."""

    status: Literal["ok", "unknown"]
    reason: str | None = None
    items: list[YoutubeVideoStat] = Field(default_factory=list)
    missing_records: list[str] = Field(default_factory=list)
    checked_at: str


class YoutubeUploadsListInput(AxModel):
    limit: int = Field(default=50, ge=1, le=200)
    # 주면 그 캠페인(cmp_…)·의뢰(wr_…)에 연결된 영상만
    campaign_id: str | None = Field(default=None, min_length=1, max_length=80)


class YoutubeUpload(AxModel):
    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    video_id: str
    url: str | None = None
    uploaded_at: str | None = None
    title: str | None = None
    privacy: str | None = None
    campaign_id: str | None = None  # 기록에 연결된 캠페인·의뢰 번호(없으면 None)


class YoutubeUploadsListOutput(AxModel):
    items: list[YoutubeUpload] = Field(default_factory=list)
    total: int = 0
