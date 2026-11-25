"""마케팅 이미지 시트·스토리보드 제작 기록 읽기 (read, 2026-11-20).

이미지 시트(`character_sheet_prompt`)를 만들 때 남긴 기록과, 그 기록에 이어 붙인 스토리보드
(`storyboard_prompt`)를 기록 페이지가 읽는다. 기록 번호는 `CR-` + 12자리 소문자 16진수.
올린 원본 이미지는 저장하지 않고 작은 미리보기(JPEG base64)만 둔다.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from ax_contracts.schemas.common import AxModel
from ax_contracts.schemas.marketing_character import CharacterReference, CharacterUploadKind

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
    # 대본(11/23, 추가): 장면 수(없으면 0)와 저장 시각
    script_scenes: int = 0
    script_at: str | None = None
    # 11/24 올린 이미지 종류 (marketing_character.CharacterUploadKind, 옛 기록은 real_photo)
    kind: CharacterUploadKind = "real_photo"
    # 11/25 참조 이미지 목록(0번 대표). 옛 기록은 빈 목록 = 대표만
    references: list[CharacterReference] = Field(default_factory=list)


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
    # 대본(11/23, 추가). CreativeScript 모양(marketing_script.py). 옛 기록은 None
    script: dict[str, Any] | None = None
    # Higgsfield 그림·영상:
    # {sheet_image|board_image|video: {status, error, asset, sha256, review_id, at}}
    # 자막 얹기(11/25, 추가): overlay {status, error, captions, source_part, ...}, overlay_video {…}
    render: dict[str, Any] = Field(default_factory=dict)


CREATIVE_VIDEO_REQUEST_SKILL = "creative_video_request"
# 렌더 단계 상태: running(만드는 중) · done · failed · blocked(관문·예산) · unavailable(설정 없음)
# · awaiting_approval(영상, 사람 승인 대기) · rejected(영상, 거절)


class CreativeVideoRequestInput(AxModel):
    """스토리보드 그림으로 영상 만들기를 사람 승인에 올린다(C `generate_creative_video`)."""

    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    who: str | None = Field(default=None, max_length=80)  # 요청한 사람(대시보드 사람 토큰)
    # 11/26 추가: 다 만든 영상을 다시 만들기 (화면 버튼에서만). 그 기록 render.qc에 warn이 있을
    # 때만 done이어도 새 멱등 키로 영상 승인을 다시 요청한다(사람 승인·영상 예산 그대로)
    regenerate: bool = False


# 11/26 추가: 영상 기술 검사(ffmpeg) 결과. 기록 render.qc 모양
# (`marketing_agent.creative_qc`). 자막·소리는 우리 후처리가 얹어 늘 있으므로 검사하지 않는다
CreativeQcCheckId = Literal["black", "freeze", "duration"]
CreativeQcStatus = Literal["pass", "warn", "error"]  # error = 확인 못 함(ffmpeg 없음·실패)


class CreativeQcCheck(AxModel):
    id: CreativeQcCheckId
    status: CreativeQcStatus
    detail_ko: str
    ranges: list[list[float]] = Field(default_factory=list)  # [[시작 초, 끝 초], …]


class CreativeVideoQc(AxModel):
    video_sha: str | None = None  # 검사한 영상 파일 sha256 (같으면 다시 돌리지 않는다)
    part: str | None = None  # 검사한 칸 (overlay_video · final_video · video)
    checks: list[CreativeQcCheck] = Field(default_factory=list)
    at: str | None = None


class CreativeVideoRequestOutput(AxModel):
    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    video: dict[str, Any]  # render.video


CREATIVE_RECORD_ATTACH_SKILL = "creative_record_attach"
# 사람이 직접 올릴 수 있는 칸: Higgsfield 칸 3개 + 완성 영상(다른 도구로 만든 최종본)
# 11/25 motion_video: 모션 참조 영상(영상 모델이 동작만 따른다, 추가)
CREATIVE_ATTACH_PARTS = ("sheet_image", "board_image", "video", "final_video", "motion_video")


class CreativeRecordAttachInput(AxModel):
    """사람이 올린 그림·영상을 제작 기록에 붙인다(A `attach_creative_file`, 2026-11-20).

    파일은 dashboard-api가 미디어 폴더 `creative/<기록>/`에 먼저 저장하고 이름·sha256만 넘긴다.
    """

    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    part: str = Field(pattern=r"^(sheet_image|board_image|video|final_video|motion_video)$")
    asset: str = Field(pattern=r"^(sheet|board|video|final)\.(jpg|png|mp4)$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)
    label: str | None = Field(default=None, max_length=120)  # 올린 파일 이름
    who: str | None = Field(default=None, max_length=80)  # 올린 사람(대시보드 사람 토큰)


class CreativeRecordAttachOutput(AxModel):
    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    part: str
    item: dict[str, Any]  # render.<part>


# ---- 키프레임 (2026-11-25, 추가). 상태는 기록의 render.keyframes
# {status, frames: [{index, at_s, scene_n, svg_source("planning"|"none"), svg, prompt_en,
#  layout_asset, layout_png_media_id, asset, media_id, sha256, status, error, credits_estimate}],
#  approved_at}. 키프레임 영상 모델(Kling 4.0)로 만들 때만 생긴다
CREATIVE_KEYFRAME_REDRAW_SKILL = "creative_keyframe_redraw"
MAX_KEYFRAMES = 10  # start_image + 중간 image_references + end_image 합계


class CreativeKeyframeRedrawInput(AxModel):
    """키프레임 한 장 [다시 그리기] (그림 1장, 지금 그림 관문 B·그림 크레딧 그대로).

    결과는 `CreativeRecordAttachOutput`(part="keyframes", item=render.keyframes)."""

    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    index: int = Field(ge=0, lt=MAX_KEYFRAMES)


# ---- 유튜브 올리기 (2026-11-23). 상태는 기록의 render.youtube
CREATIVE_YOUTUBE_DRAFT_SKILL = "creative_youtube_draft"
CREATIVE_YOUTUBE_REQUEST_SKILL = "creative_youtube_request"
YOUTUBE_STATUS_SKILL = "youtube_status"
# 유튜브 영상이 어느 캠페인 영상인지(11/23, 선택). 빈 문자열이면 연결을 푼다
YOUTUBE_CAMPAIGN_LINK_PATTERN = r"^(cmp_[A-Za-z0-9][A-Za-z0-9_-]{0,63})?$"


class CreativeYoutubeDraftInput(AxModel):
    """AI 초안 만들기(처음·`regenerate`)나 사람이 고친 값 저장. 비운 칸은 그대로 둔다."""

    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    regenerate: bool = False
    title: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=5000)
    tags: list[str] | None = Field(default=None, max_length=15)
    privacy: str | None = Field(default=None, pattern=r"^(private|unlisted|public)$")
    part: str | None = Field(default=None, pattern=r"^(video|final_video|overlay_video)$")
    # 어느 캠페인(cmp_…) 영상인지(11/23, 추가). 비우면(None) 그대로, ""면 연결을 푼다.
    # 올린 뒤에도 바꿀 수 있다(올리기와 상관없는 표시라 승인이 필요 없다)
    campaign_id: str | None = Field(default=None, pattern=YOUTUBE_CAMPAIGN_LINK_PATTERN)


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
    # 채널 주소(연결됐을 때. @핸들이 있으면 youtube.com/@핸들, 없으면 channel/<id>) — 11/23
    channel_url: str | None = None


class InstagramStatusInput(AxModel):
    """입력 없음(지금 회사의 인스타 연결 상태를 본다)."""


class InstagramStatusOutput(AxModel):
    """인스타 연결: missing_keys · invalid_credentials · ok · error. 토큰 값은 없다(11/23)."""

    connection: str
    username: str | None = None
    profile_url: str | None = None
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
    # 어느 캠페인(cmp_…) 영상인지(11/23, 추가). 비우면(None) 그대로, ""면 연결을 푼다
    campaign_id: str | None = Field(default=None, pattern=YOUTUBE_CAMPAIGN_LINK_PATTERN)


class RequestVideoYoutubeRequestInput(AxModel):
    """초안대로 유튜브에 올리기를 사람 승인(C `publish_youtube`)에 올린다."""

    post_draft_id: str = Field(pattern=REQUEST_VIDEO_DRAFT_ID_PATTERN)
    who: str | None = Field(default=None, max_length=80)


class RequestVideoYoutubeOutput(AxModel):
    post_draft_id: str = Field(pattern=REQUEST_VIDEO_DRAFT_ID_PATTERN)
    request_id: str | None = None  # 의뢰 번호(wr_…)
    youtube: dict[str, Any]  # 제작 기록 render.youtube와 같은 모양


# ---- 인스타그램 올리기 (2026-11-23). 제작 기록 영상·의뢰로 만든 영상을 회사 인스타 계정에
# 릴스로 올린다. 유튜브 올리기와 같은 흐름이다: AI 캡션·해시태그 초안(사람이 고친다)
# → 올리기 요청은 늘 사람 승인(C `publish_instagram_video`) → 승인되면 AI 라벨을 입힌 영상만
# 릴스로 게시하고 게시물 주소(permalink)를 남긴다.
# 상태는 제작 기록 render.instagram, 의뢰 영상은 post_draft_id별 표
CREATIVE_INSTAGRAM_DRAFT_SKILL = "creative_instagram_draft"
CREATIVE_INSTAGRAM_REQUEST_SKILL = "creative_instagram_request"
REQUEST_VIDEO_INSTAGRAM_DRAFT_SKILL = "request_video_instagram_draft"
REQUEST_VIDEO_INSTAGRAM_REQUEST_SKILL = "request_video_instagram_request"
INSTAGRAM_CAPTION_MAX = 2200
INSTAGRAM_HASHTAG_MAX = 5


class CreativeInstagramDraftInput(AxModel):
    """AI 캡션·해시태그 초안 만들기(처음·`regenerate`)나 사람이 고친 값 저장. 비운 칸은 그대로."""

    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    regenerate: bool = False
    caption: str | None = Field(default=None, max_length=INSTAGRAM_CAPTION_MAX)
    hashtags: list[str] | None = Field(default=None, max_length=INSTAGRAM_HASHTAG_MAX)
    part: str | None = Field(default=None, pattern=r"^(video|final_video|overlay_video)$")


class CreativeInstagramRequestInput(AxModel):
    """초안대로 인스타 릴스 올리기를 사람 승인(C `publish_instagram_video`)에 올린다."""

    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    who: str | None = Field(default=None, max_length=80)


class CreativeInstagramOutput(AxModel):
    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    instagram: dict[str, Any]  # render.instagram


class RequestVideoInstagramDraftInput(AxModel):
    """의뢰 영상의 AI 캡션·해시태그 초안 또는 사람이 고친 값. 올리는 파일은 늘 AI 라벨 영상이다."""

    post_draft_id: str = Field(pattern=REQUEST_VIDEO_DRAFT_ID_PATTERN)
    regenerate: bool = False
    caption: str | None = Field(default=None, max_length=INSTAGRAM_CAPTION_MAX)
    hashtags: list[str] | None = Field(default=None, max_length=INSTAGRAM_HASHTAG_MAX)


class RequestVideoInstagramRequestInput(AxModel):
    """초안대로 의뢰 영상의 인스타 릴스 올리기를 사람 승인(C `publish_instagram_video`)에 올린다."""

    post_draft_id: str = Field(pattern=REQUEST_VIDEO_DRAFT_ID_PATTERN)
    who: str | None = Field(default=None, max_length=80)


class RequestVideoInstagramOutput(AxModel):
    post_draft_id: str = Field(pattern=REQUEST_VIDEO_DRAFT_ID_PATTERN)
    request_id: str | None = None  # 의뢰 번호(wr_…)
    instagram: dict[str, Any]  # 제작 기록 render.instagram과 같은 모양


# ---- 의뢰로 만든 영상(wr_…)의 썸네일 후보 고르기 (2026-11-23). 후보 3장은 라벨 입힌 영상에서 뽑은
# 그림(AI 표시 포함)이다. 고르지 않으면 첫 후보가 기본값이고, 게시·유튜브 승인 해시에 들어간다
REQUEST_VIDEO_COVER_SKILL = "request_video_cover_select"
REQUEST_VIDEO_COVER_ID_PATTERN = r"^cov_[0-9a-f]{12}$"


class RequestVideoCoverSelectInput(AxModel):
    """후보(`covers[].media_id`) 안의 값만 받는다. 승인 대기·승인 뒤에 바꾸면 그 승인은 무효다."""

    post_draft_id: str = Field(pattern=REQUEST_VIDEO_DRAFT_ID_PATTERN)
    media_id: str = Field(pattern=REQUEST_VIDEO_COVER_ID_PATTERN)
    who: str | None = Field(default=None, max_length=80)


class RequestVideoCoverOutput(AxModel):
    post_draft_id: str = Field(pattern=REQUEST_VIDEO_DRAFT_ID_PATTERN)
    cover_media_id: str = Field(pattern=REQUEST_VIDEO_COVER_ID_PATTERN)
    changed: bool  # 같은 것을 다시 고르면 false(승인은 그대로)
    cover_rev: int = Field(ge=0)
    approvals_reset: list[str] = Field(default_factory=list)  # 무효가 된 승인: instagram·youtube


# ---- 제작 기록 영상의 썸네일 후보 3장 (2026-11-23). Higgsfield로 만든 그림(AI 표시 XMP 포함)이고
# 상태는 기록의 render.thumbnails. 만들기는 사람 결정(C `generate_creative_thumbnails`) 뒤에만,
# 고르지 않으면 첫 후보가 기본값이고 유튜브 승인 해시에 들어간다
CREATIVE_THUMBNAILS_REQUEST_SKILL = "creative_thumbnails_request"
CREATIVE_THUMBNAIL_SELECT_SKILL = "creative_thumbnail_select"
CREATIVE_THUMBNAIL_ID_PATTERN = r"^thm_[0-9a-f]{12}$"


class CreativeThumbnailsRequestInput(AxModel):
    """후보 3장 만들기를 사람 결정에 올린다. 후보가 이미 있으면 `regenerate`일 때만 다시 만든다."""

    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    regenerate: bool = False
    who: str | None = Field(default=None, max_length=80)


class CreativeThumbnailsOutput(AxModel):
    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    # render.thumbnails: {status, error, candidates[{media_id, kind, sha256, is_ai_generated}],
    # chosen, cover_rev, review_id, aspect, ...}
    thumbnails: dict[str, Any]


class CreativeThumbnailSelectInput(AxModel):
    """후보(`render.thumbnails.candidates[].media_id`) 안의 값만. 승인 대기면 그 승인은 무효."""

    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    media_id: str = Field(pattern=CREATIVE_THUMBNAIL_ID_PATTERN)
    who: str | None = Field(default=None, max_length=80)


class CreativeThumbnailSelectOutput(AxModel):
    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    cover_media_id: str = Field(pattern=CREATIVE_THUMBNAIL_ID_PATTERN)
    changed: bool  # 같은 것을 다시 고르면 false(승인은 그대로)
    cover_rev: int = Field(ge=0)
    approvals_reset: list[str] = Field(default_factory=list)  # 무효가 된 승인: youtube


# ---- 자막·엔드카드 얹기 (2026-11-25). 영상이 붙으면 코드(ffmpeg)로 자동 한 번,
# 사람이 자막을 고치면 `creative_overlay_redo`로 다시. 크레딧 0·안전 관문 없음.
# 상태는 기록의 render.overlay
# {status: applied|skipped|failed|running, error, captions, source_part: video|final_video},
# 결과 영상은 render.overlay_video(파일 overlay.mp4)
CREATIVE_OVERLAY_REDO_SKILL = "creative_overlay_redo"


class CreativeOverlayCaption(AxModel):
    no: int = Field(ge=1, le=12)  # 스토리보드 칸 번호
    caption_ko: str = Field(default="", max_length=60)  # 비우면 그 칸은 자막 없음


class CreativeOverlayRedoInput(AxModel):
    """사람이 자막을 고친 뒤 최신 영상에 다시 얹는다(read: 기록만 바꾸고 외부 호출·비용 없음)."""

    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    captions: list[CreativeOverlayCaption] = Field(min_length=1, max_length=12)
    who: str | None = Field(default=None, max_length=80)


class CreativeOverlayRedoOutput(AxModel):
    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    overlay: dict[str, Any]  # render.overlay
    overlay_video: dict[str, Any] = Field(default_factory=dict)  # render.overlay_video (없으면 빈)
