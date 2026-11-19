"""마케팅 캐릭터 시트 이미지 프롬프트 (read, 2026-11-19).

마케팅 화면에 캐릭터 이미지 한 장(그림·사진)을 올리면, 마케팅 AI가 그 캐릭터를 읽고 같은
캐릭터의 방향(턴어라운드)·표정·포즈·색 팔레트가 한 장에 다 들어간 "캐릭터 시트" 이미지
프롬프트를 만든다. 이미지는 만들지 않는다(프롬프트만).

- 이미지와 메모는 사람이 올린 외부 입력이라 `AgentRequest.untrusted`로만 받는다
  (`untrusted.image` = `CharacterSheetImage`, `untrusted.note` = 한 줄 메모, 선택).
- 방향 칸(`SHEET_VIEWS`)과 표정 칸(`SHEET_EXPRESSIONS`)은 코드가 정해서 빠지지 않는다.
- 이미지에서 본 것과, 보이지 않아 추정한 것을 나눠 돌려준다(`seen_ko`·`guessed_ko`).
- 실제 사람 사진이면 그 사람을 그대로 복제하지 않고 그림체 캐릭터로 바꾼다(`stylized_from_photo`).
- 올린 이미지는 저장하지 않는다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ax_contracts.schemas.common import AxModel

CHARACTER_SHEET_SKILL = "character_sheet_prompt"

# 원본 이미지 한 장 한도(바이트). base64로는 약 4/3배가 된다
MAX_CHARACTER_IMAGE_BYTES = 2 * 1024 * 1024
MAX_CHARACTER_IMAGE_BASE64 = (MAX_CHARACTER_IMAGE_BYTES * 4) // 3 + 4
MAX_CHARACTER_NOTE = 200

# 시트의 방향 칸 (id, 한국어 이름, 영어 프롬프트 조각)
SHEET_VIEWS: tuple[tuple[str, str, str], ...] = (
    ("front", "정면", "front view"),
    ("back", "뒷면", "back view"),
    ("left", "왼쪽", "left side view (profile facing left)"),
    ("right", "오른쪽", "right side view (profile facing right)"),
    ("top", "위에서 본 모습", "top-down view from above"),
    ("bottom", "아래에서 본 모습", "low-angle view from below"),
    ("three_quarter", "3/4 각도", "three-quarter view"),
)
# 시트의 표정 칸
SHEET_EXPRESSIONS: tuple[tuple[str, str, str], ...] = (
    ("smile", "웃는 얼굴", "smiling, warm and friendly"),
    ("neutral", "무표정", "neutral expression, not smiling"),
    ("angry", "화남", "angry, frowning"),
    ("surprised", "놀람", "surprised, eyes wide open"),
    ("sad", "슬픔", "sad, downcast eyes"),
)

PanelKind = Literal["view", "expression", "pose", "palette", "details"]


class CharacterSheetImage(AxModel):
    """올린 캐릭터 이미지 한 장 (`untrusted.image`). 저장하지 않는다."""

    media_type: Literal["image/png", "image/jpeg", "image/webp"]
    data_base64: str = Field(min_length=1, max_length=MAX_CHARACTER_IMAGE_BASE64)


class CharacterSheetInput(AxModel):
    """입력 칸은 없다. 이미지와 메모는 `untrusted`로 받는다."""


class PaletteColor(AxModel):
    name: str = Field(min_length=1, max_length=40)
    hex: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    use: str | None = Field(default=None, max_length=60)  # 머리·재킷 등 어디에 쓰는지


class CharacterSheetPanel(AxModel):
    kind: PanelKind
    id: str = Field(min_length=1, max_length=40)
    label_ko: str = Field(min_length=1, max_length=40)
    prompt_en: str = Field(min_length=1, max_length=400)


class CharacterSheetPromptOutput(AxModel):
    summary_ko: str = Field(min_length=1, max_length=300)  # 캐릭터 특징 요약
    personality_ko: str | None = Field(default=None, max_length=200)  # 이미지에서 느껴지는 성격
    seen_ko: list[str] = Field(default_factory=list)  # 이미지에서 본 것
    guessed_ko: list[str] = Field(default_factory=list)  # 이미지에서 보이지 않아 추정한 것
    stylized_from_photo: bool = False  # 실제 사람 사진이라 그림체 캐릭터로 바꿨는지
    note_applied: str | None = Field(default=None, max_length=200)  # 반영한 메모
    appearance_en: str = Field(min_length=1, max_length=600)  # 고정 외형 (얼굴형·머리·눈·체형)
    outfit_en: str = Field(min_length=1, max_length=400)
    proportions: str = Field(min_length=1, max_length=80)  # 키 비율 (예: 6 heads tall)
    palette: list[PaletteColor] = Field(min_length=3, max_length=8)
    consistency_en: str = Field(
        min_length=1, max_length=1500
    )  # 모든 칸에서 같은 캐릭터로 고정하는 문장
    panels: list[CharacterSheetPanel] = Field(min_length=1)
    layout_ko: list[str] = Field(min_length=1)  # 칸 배치 설명
    prompt_en: str = Field(min_length=1, max_length=8000)  # 이미지 생성기에 바로 붙이는 프롬프트
    negative_prompt_en: str = Field(min_length=1, max_length=1000)
