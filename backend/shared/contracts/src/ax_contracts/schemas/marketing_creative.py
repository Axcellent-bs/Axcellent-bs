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
