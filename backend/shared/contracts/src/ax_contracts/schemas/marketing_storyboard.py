"""마케팅 스토리보드 이미지 프롬프트 (read, 2026-11-20).

이미지 시트(캐릭터 시트)에서 나온 캐릭터 고정 문장과 사람이 적은 줄거리로, 장면마다 구도
(샷 크기·카메라 앵글)를 정한 스토리보드 칸과 칸별 이미지 프롬프트, 한 장짜리 스토리보드 시트
프롬프트를 만든다. 이미지는 만들지 않는다(프롬프트만).

- 줄거리·캐릭터 문장·그림체는 사람이 넣은 외부 입력이라 `AgentRequest.untrusted`로만 받는다
  (`untrusted.story`, `untrusted.character_en`, `untrusted.style_en`, `untrusted.shots`).
- 샷 크기(`SHOT_SIZES`)와 앵글(`CAMERA_ANGLES`) 이름·영어 문장은 코드 표에서만 나온다.
  LLM은 표의 id만 고르고, 모르는 값이면 코드가 기본값으로 바꾼다.
- 칸 수는 `SHOT_COUNTS` 중 하나(기본 12). 시트는 3열로 놓는다.
"""

from __future__ import annotations

from pydantic import Field

from ax_contracts.schemas.common import AxModel

STORYBOARD_SKILL = "storyboard_prompt"

MAX_STORY = 1000
MAX_CHARACTER_EN = 1500
MAX_STYLE_EN = 200
SHOT_COUNTS: tuple[int, ...] = (6, 9, 12)
DEFAULT_SHOTS = 12
STORYBOARD_COLUMNS = 3

# 샷 크기 (id, 한국어 이름, 영어 프롬프트 조각)
SHOT_SIZES: tuple[tuple[str, str, str], ...] = (
    (
        "extreme_wide",
        "아주 먼 풍경",
        "extreme wide establishing shot, tiny figures in a vast landscape",
    ),
    ("wide", "풍경 속 전신", "wide shot, full body small in the frame with the surroundings"),
    ("full", "전신", "full shot, whole body from head to toe"),
    ("medium", "허리 위", "medium shot, framed from the waist up"),
    ("close_up", "얼굴", "close-up shot of the face"),
    ("extreme_close_up", "아주 가까이", "extreme close-up on a small detail"),
    ("detail", "손·물건", "insert detail shot of hands or an object"),
    ("over_shoulder", "어깨 너머", "over-the-shoulder shot"),
    ("pov", "인물 눈으로", "point-of-view shot, seen through the character's eyes"),
)
# 카메라 앵글
CAMERA_ANGLES: tuple[tuple[str, str, str], ...] = (
    ("eye_level", "눈높이", "eye-level camera"),
    ("low", "아래에서 올려다봄", "low-angle camera looking up"),
    ("high", "위에서 내려다봄", "high-angle camera looking down"),
    ("birds_eye", "바로 위에서", "bird's-eye view from directly above"),
    ("ground", "땅바닥 높이", "ground-level camera close to the ground"),
    ("dutch", "기울어진 화면", "dutch angle, tilted horizon"),
)


class StoryboardInput(AxModel):
    """입력 칸은 없다. 줄거리·캐릭터 문장·그림체·칸 수는 `untrusted`로 받는다."""


class StoryboardShot(AxModel):
    no: int = Field(ge=1, le=12)
    scene_ko: str = Field(min_length=1, max_length=200)  # 이 칸에서 보이는 장면
    shot_size: str = Field(min_length=1, max_length=40)  # SHOT_SIZES id
    shot_size_ko: str = Field(min_length=1, max_length=40)
    angle: str = Field(min_length=1, max_length=40)  # CAMERA_ANGLES id
    angle_ko: str = Field(min_length=1, max_length=40)
    composition_ko: str = Field(min_length=1, max_length=200)  # 화면 안 배치 (어디에 무엇)
    has_character: bool  # 캐릭터가 화면에 나오는지 (풍경·물건만인 칸은 false)
    prompt_en: str = Field(min_length=1, max_length=1500)


class StoryboardPromptOutput(AxModel):
    title_ko: str = Field(min_length=1, max_length=80)
    logline_ko: str = Field(min_length=1, max_length=300)  # 한 줄 줄거리
    style_en: str = Field(min_length=1, max_length=MAX_STYLE_EN)
    character_used: bool  # 이미지 시트 캐릭터 문장을 넣었는지
    columns: int = Field(ge=1, le=6)
    shots: list[StoryboardShot] = Field(min_length=1, max_length=12)
    sheet_prompt_en: str = Field(min_length=1, max_length=12000)  # 한 장짜리 스토리보드 시트
    negative_prompt_en: str = Field(min_length=1, max_length=1000)
    record_id: str | None = None  # 붙인 제작 기록 번호. 붙이지 않았으면 None
