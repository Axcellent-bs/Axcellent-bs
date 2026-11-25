"""회사 대표 캐릭터 (2026-11-23, 홍보 영상 의뢰에 회사 캐릭터 붙이기).

새 저장소를 만들지 않고 제작 기록(CR-…) 한 줄에 "대표 캐릭터" 표시를 단다. 회사마다 최대
`MAX_BRAND_CHARACTERS`개이고, 마지막에 표시한 것이 기본이다. 다른 회사 기록은 보이지 않는다.

- 표시하려면 사람이 "우리 회사가 쓸 권리가 있는 그림"이라고 확인해야 한다(`rights_confirmed`).
  확인한 사람·때를 기록에 남긴다. 표시를 떼도 제작 기록은 지우지 않는다.
- 미리보기는 기록의 작은 JPEG(`thumbnail_base64`)와, 시트 그림이 있으면 그 파일의 부분 이름
  (`preview_part` = sheet_image, dashboard-api `/marketing/creative-records/{id}/asset/{part}`).
- 홍보 영상 의뢰에서 고른 대표 캐릭터 번호는 총괄 의뢰 기록의 `brand_character_record_id`에
  적히고, 마케팅 `prepare_video_draft_from_plan` 입력 `brand_character_record_id`로 넘어간다.
"""

from __future__ import annotations

from pydantic import Field

from ax_contracts.schemas.common import AxModel
from ax_contracts.schemas.marketing_character import CharacterReference, CharacterUploadKind
from ax_contracts.schemas.marketing_creative import RECORD_ID_PATTERN

BRAND_CHARACTERS_LIST_SKILL = "brand_characters_list"
BRAND_CHARACTER_MARK_SKILL = "brand_character_mark"
BRAND_CHARACTER_UNMARK_SKILL = "brand_character_unmark"
MAX_BRAND_CHARACTERS = 3
MAX_BRAND_NAME = 80


class BrandCharacter(AxModel):
    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    name: str = Field(max_length=MAX_BRAND_NAME)  # 이미지 시트 요약(없으면 메모·번호)
    thumbnail_base64: str | None = None
    preview_part: str | None = None  # "sheet_image"면 시트 그림 파일이 있다
    saved_at: str  # 대표로 표시한 때
    is_default: bool = False  # 마지막에 표시한 것
    marked_by: str | None = None
    rights_confirmed_by: str | None = None
    rights_confirmed_at: str | None = None
    # 11/24 올린 이미지 종류 (CharacterUploadKind, 옛 기록은 real_photo)
    kind: CharacterUploadKind = "real_photo"
    # 11/25 참조 이미지 목록(0번 대표). 옛 기록은 빈 목록 = 대표만
    references: list[CharacterReference] = Field(default_factory=list)


class BrandCharactersListInput(AxModel):
    """입력 칸 없음. 요청 회사(`meta.tenant_id`)의 대표 캐릭터만."""


class BrandCharactersListOutput(AxModel):
    items: list[BrandCharacter] = Field(default_factory=list, max_length=MAX_BRAND_CHARACTERS)


class BrandCharacterMarkInput(AxModel):
    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    # 사람이 권리를 확인했는가. true가 아니면 거절(rights_not_confirmed)
    rights_confirmed: bool = False
    who: str | None = Field(default=None, max_length=80)  # dashboard-api가 사람 토큰에서 채운다


class BrandCharacterUnmarkInput(AxModel):
    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    who: str | None = Field(default=None, max_length=80)


class BrandCharacterMarkOutput(AxModel):
    record_id: str = Field(pattern=RECORD_ID_PATTERN)
    marked: bool
    # 4개째를 표시해 표시가 떨어진 가장 오래된 대표 캐릭터(없으면 None)
    dropped: str | None = None
    items: list[BrandCharacter] = Field(default_factory=list, max_length=MAX_BRAND_CHARACTERS)
