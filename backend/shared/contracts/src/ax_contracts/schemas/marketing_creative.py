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
