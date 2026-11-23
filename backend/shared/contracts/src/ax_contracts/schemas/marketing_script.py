"""마케팅 영상 대본(스크립트) (read, 2026-11-23).

흐름: 캐릭터 이미지 → 이미지 시트(`character_sheet_prompt`) → (줄거리가 있으면) 대본
(`creative_script`) → 대본 장면을 따라 스토리보드(`storyboard_prompt`) → 대본+스토리보드로 영상.

- 대본은 제작 기록(CR-…)에 붙는다(`CreativeRecordGetOutput.script`).
- 줄거리는 사람이 넣은 외부 입력이라 `AgentRequest.untrusted.story`로만 받는다.
- LLM이 없으면 줄거리 문장을 장면에 나눠 담은 단순 대본(`source="rule"`)을 만든다.
- 줄거리에 없는 숫자·%(가격·할인·수치)가 든 문장은 코드가 뺀다(`dropped_ko`에 남긴다).
- 사람이 고친 대본은 `creative_script_save`로 저장한다(`source="human"`). 고친 값은 그대로 둔다.
"""

from __future__ import annotations

from pydantic import Field

from ax_contracts.schemas.common import AxModel

CREATIVE_SCRIPT_SKILL = "creative_script"
CREATIVE_SCRIPT_SAVE_SKILL = "creative_script_save"
SCRIPT_RECORD_ID_PATTERN = r"^CR-[0-9a-f]{12}$"

SCRIPT_SCENE_COUNTS: tuple[int, ...] = (6, 9, 12)  # 스토리보드 칸 수와 같다
DEFAULT_SCRIPT_SCENES = 6
SCRIPT_SECONDS: tuple[int, ...] = (15, 30, 60)
DEFAULT_SCRIPT_SECONDS = 30
MAX_SCRIPT_SCENES = 12
MAX_SCRIPT_TITLE = 80
MAX_SCRIPT_VISUAL = 200
MAX_SCRIPT_LINE = 200
MAX_SCRIPT_CAPTION = 60
MAX_SCRIPT_SPEAKER = 40
MAX_SCRIPT_CTA = 120


class ScriptScene(AxModel):
    no: int = Field(ge=1, le=MAX_SCRIPT_SCENES)
    start_s: float = Field(ge=0, le=600)
    end_s: float = Field(gt=0, le=600)
    visual_ko: str = Field(min_length=1, max_length=MAX_SCRIPT_VISUAL)  # 화면 설명
    speaker_ko: str = Field(
        default="", max_length=MAX_SCRIPT_SPEAKER
    )  # 말하는 사람(내레이션이면 비움)
    line_ko: str = Field(default="", max_length=MAX_SCRIPT_LINE)  # 대사·내레이션
    caption_ko: str = Field(default="", max_length=MAX_SCRIPT_CAPTION)  # 자막(화면 글자)


class CreativeScript(AxModel):
    title_ko: str = Field(min_length=1, max_length=MAX_SCRIPT_TITLE)
    total_seconds: int = Field(ge=1, le=600)
    scenes: list[ScriptScene] = Field(min_length=1, max_length=MAX_SCRIPT_SCENES)
    cta_ko: str = Field(default="", max_length=MAX_SCRIPT_CTA)  # 끝 장면의 행동 유도 문장
    source: str = Field(pattern=r"^(llm|rule|human)$")  # 누가 썼나
    dropped_ko: list[str] = Field(
        default_factory=list, max_length=40
    )  # 줄거리에 없는 숫자로 뺀 문장
    edited_by: str | None = Field(default=None, max_length=80)
    at: str | None = None  # 저장 시각(ISO)


class CreativeScriptInput(AxModel):
    """AI로 대본 쓰기(처음·다시 쓰기). 줄거리는 `untrusted.story`(비우면 기록의 줄거리)."""

    record_id: str = Field(pattern=SCRIPT_RECORD_ID_PATTERN)
    scenes: int = Field(default=DEFAULT_SCRIPT_SCENES, ge=1, le=MAX_SCRIPT_SCENES)
    seconds: int = Field(default=DEFAULT_SCRIPT_SECONDS, ge=5, le=600)


class CreativeScriptSaveInput(AxModel):
    """사람이 고친 대본을 저장한다. 장면 번호는 1부터 차례로 다시 매긴다."""

    record_id: str = Field(pattern=SCRIPT_RECORD_ID_PATTERN)
    title_ko: str = Field(min_length=1, max_length=MAX_SCRIPT_TITLE)
    scenes: list[ScriptScene] = Field(min_length=1, max_length=MAX_SCRIPT_SCENES)
    cta_ko: str = Field(default="", max_length=MAX_SCRIPT_CTA)
    who: str | None = Field(default=None, max_length=80)  # 고친 사람(대시보드 사람 토큰)


class CreativeScriptOutput(AxModel):
    record_id: str = Field(pattern=SCRIPT_RECORD_ID_PATTERN)
    script: CreativeScript
