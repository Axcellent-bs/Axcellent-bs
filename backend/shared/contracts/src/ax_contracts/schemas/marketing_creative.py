"""마케팅 이미지 시트·스토리보드 제작 기록 읽기 (read, 2026-11-20).

이미지 시트(`character_sheet_prompt`)를 만들 때 남긴 기록과, 그 기록에 이어 붙인 스토리보드
(`storyboard_prompt`)를 기록 페이지가 읽는다. 기록 번호는 `CR-` + 12자리 소문자 16진수.
올린 원본 이미지는 저장하지 않고 작은 미리보기(JPEG base64)만 둔다.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from ax_contracts.schemas.common import AxModel

CREATIVE_RECORDS_LIST_SKILL = "creative_records_list"
CREATIVE_RECORD_GET_SKILL = "creative_record_get"
RECORD_ID_PATTERN = r"^CR-[0-9a-f]{12}$"


class CreativeRecordsListInput(AxModel):
    limit: int = Field(default=50, ge=1, le=200)


class CreativeRecordSummary(AxModel):
    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    created_at: str
    summary_ko: str
    note: str | None = None
    story: str | None = None
    thumbnail_base64: str | None = None
    storyboard_title_ko: str | None = None
    shot_count: int = 0
    storyboard_at: str | None = None
    render: dict[str, str] = Field(default_factory=dict)  # 단계별 상태(Higgsfield 그림·영상)


class CreativeRecordsListOutput(AxModel):
    items: list[CreativeRecordSummary]


class CreativeRecordGetInput(AxModel):
    record_id: str = Field(pattern=RECORD_ID_PATTERN)


class CreativeRecordGetOutput(AxModel):
    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    created_at: str
    note: str | None = None
    story: str | None = None
    shots: int | None = None
    thumbnail_base64: str | None = None
    sheet: dict[str, Any]  # CharacterSheetPromptOutput 모양
    storyboard: dict[str, Any] | None = None  # StoryboardPromptOutput 모양
    storyboard_at: str | None = None
    # Higgsfield 그림·영상:
    # {sheet_image|board_image|video: {status, error, asset, sha256, review_id, at}}
    render: dict[str, Any] = Field(default_factory=dict)


CREATIVE_VIDEO_REQUEST_SKILL = "creative_video_request"
# 렌더 단계 상태: running(만드는 중) · done · failed · blocked(관문·예산) · unavailable(설정 없음)
# · awaiting_approval(영상, 사람 승인 대기) · rejected(영상, 거절)


class CreativeVideoRequestInput(AxModel):
    """스토리보드 그림으로 영상 만들기를 사람 승인에 올린다(C `generate_creative_video`)."""

    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    who: str | None = Field(default=None, max_length=80)  # 요청한 사람(대시보드 사람 토큰)


class CreativeVideoRequestOutput(AxModel):
    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    video: dict[str, Any]  # render.video


CREATIVE_RECORD_ATTACH_SKILL = "creative_record_attach"
# 사람이 직접 올릴 수 있는 칸: Higgsfield 칸 3개 + 완성 영상(다른 도구로 만든 최종본)
CREATIVE_ATTACH_PARTS = ("sheet_image", "board_image", "video", "final_video")


class CreativeRecordAttachInput(AxModel):
    """사람이 올린 그림·영상을 제작 기록에 붙인다(A `attach_creative_file`, 2026-11-20).

    파일은 dashboard-api가 미디어 폴더 `creative/<기록>/`에 먼저 저장하고 이름·sha256만 넘긴다.
    """

    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    part: str = Field(pattern=r"^(sheet_image|board_image|video|final_video)$")
    asset: str = Field(pattern=r"^(sheet|board|video|final)\.(jpg|png|mp4)$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)
    label: str | None = Field(default=None, max_length=120)  # 올린 파일 이름
    who: str | None = Field(default=None, max_length=80)  # 올린 사람(대시보드 사람 토큰)


class CreativeRecordAttachOutput(AxModel):
    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    part: str
    item: dict[str, Any]  # render.<part>


# ---- 유튜브 올리기 (2026-11-23). 상태는 기록의 render.youtube
CREATIVE_YOUTUBE_DRAFT_SKILL = "creative_youtube_draft"
CREATIVE_YOUTUBE_REQUEST_SKILL = "creative_youtube_request"
YOUTUBE_STATUS_SKILL = "youtube_status"


class CreativeYoutubeDraftInput(AxModel):
    """AI 초안 만들기(처음·`regenerate`)나 사람이 고친 값 저장. 비운 칸은 그대로 둔다."""

    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    regenerate: bool = False
    title: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=5000)
    tags: list[str] | None = Field(default=None, max_length=15)
    privacy: str | None = Field(default=None, pattern=r"^(private|unlisted|public)$")
    part: str | None = Field(default=None, pattern=r"^(video|final_video)$")


class CreativeYoutubeRequestInput(AxModel):
    """초안대로 유튜브에 올리기를 사람 승인(C `publish_youtube`)에 올린다."""

    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    who: str | None = Field(default=None, max_length=80)


class CreativeYoutubeOutput(AxModel):
    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    youtube: dict[str, Any]  # render.youtube


class YoutubeStatusInput(AxModel):
    """입력 없음(지금 회사의 채널 연결 상태를 본다)."""


class YoutubeStatusOutput(AxModel):
    """채널 연결: missing_keys · invalid_credentials · ok · error. 키 값은 없다."""

    connection: str
    channel: str | None = None
    checked_at: str


# ---- 의뢰로 만든 영상(wr_…)의 유튜브 올리기 (2026-11-23). 제작 기록 유튜브와 같은 흐름이다.
# 대상은 의뢰 영상 초안 번호(post_draft_id)이고, 올리는 파일은 늘 AI 라벨을 입힌 영상이다
REQUEST_VIDEO_YOUTUBE_DRAFT_SKILL = "request_video_youtube_draft"
REQUEST_VIDEO_YOUTUBE_REQUEST_SKILL = "request_video_youtube_request"
REQUEST_VIDEO_DRAFT_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"


class RequestVideoYoutubeDraftInput(AxModel):
    """AI 초안 만들기(처음·`regenerate`)나 사람이 고친 값 저장. 비운 칸은 그대로 둔다."""

    post_draft_id: str = Field(pattern=REQUEST_VIDEO_DRAFT_ID_PATTERN)
    regenerate: bool = False
    title: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=5000)
    tags: list[str] | None = Field(default=None, max_length=15)
    privacy: str | None = Field(default=None, pattern=r"^(private|unlisted|public)$")


class RequestVideoYoutubeRequestInput(AxModel):
    """초안대로 유튜브에 올리기를 사람 승인(C `publish_youtube`)에 올린다."""

    post_draft_id: str = Field(pattern=REQUEST_VIDEO_DRAFT_ID_PATTERN)
    who: str | None = Field(default=None, max_length=80)


class RequestVideoYoutubeOutput(AxModel):
    post_draft_id: str = Field(pattern=REQUEST_VIDEO_DRAFT_ID_PATTERN)
    request_id: str | None = None  # 의뢰 번호(wr_…)
    youtube: dict[str, Any]  # 제작 기록 render.youtube와 같은 모양
