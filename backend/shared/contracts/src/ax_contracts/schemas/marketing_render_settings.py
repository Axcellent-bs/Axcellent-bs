"""회사별 만들기 설정 (2026-11-25): 그림 모델·영상 모델·그림 크레딧·영상 길이·비율.

저장은 dashboard-api(`/api/dashboard/marketing/render-settings`)가 회사 연결 파일 옆
`render_settings.json`에 하고, 마케팅이 읽는다. 마케팅 skill `render_settings_options`(read)는
화면에 보여 줄 모델 목록(회사 Higgsfield 연결의 `models_explore`, 하루 기억)과 서버 설정 기본값만
돌려준다(저장 값은 dashboard-api가 합친다).

모르는 값은 빈 값이다(None·빈 목록). 지어내지 않는다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from ax_contracts.schemas.common import AxModel

RENDER_SETTINGS_OPTIONS_SKILL = "render_settings_options"
Aspect = Literal["plan", "9:16", "1:1", "16:9"]
MAX_MODEL_ID = 120


class RenderSettingsValues(AxModel):
    """다섯 칸 모두 선택. 비우면(None) 서버 설정값(지금 동작)."""

    image_model: str | None = Field(default=None, max_length=MAX_MODEL_ID)
    video_model: str | None = Field(default=None, max_length=MAX_MODEL_ID)
    image_credits_ok: bool | None = None  # 비우면 true (그림에 크레딧을 쓴다)
    video_seconds: Literal["plan"] | int | None = None  # "plan" = 기획 길이, 정수 = 그 초(1~600)
    aspect: Aspect | None = None

    @field_validator("video_seconds")
    @classmethod
    def _seconds(cls, value: str | int | None) -> str | int | None:
        if isinstance(value, int) and not 1 <= value <= 600:
            raise ValueError("video_seconds는 1~600")
        return value


class RenderImageModel(AxModel):
    id: str = Field(max_length=MAX_MODEL_ID)
    name: str = Field(max_length=MAX_MODEL_ID)


class RenderVideoModel(AxModel):
    id: str = Field(max_length=MAX_MODEL_ID)
    name: str = Field(max_length=MAX_MODEL_ID)
    min_s: int | None = None  # 모르면 None
    max_s: int | None = None
    aspects: list[str] = Field(default_factory=list)  # 모르면 빈 목록


class RenderModels(AxModel):
    image: list[RenderImageModel] = Field(default_factory=list)
    video: list[RenderVideoModel] = Field(default_factory=list)


class RenderSettingsOptionsInput(AxModel):
    """입력 칸 없음. 요청 회사(`meta.tenant_id`)의 Higgsfield 연결로 모델을 찾는다."""


class RenderSettingsOptionsOutput(AxModel):
    defaults: RenderSettingsValues
    models: RenderModels
    models_source: Literal["higgsfield", "known"] = "known"  # known = 아는 것 + 서버 설정 모델만


class RenderVideoSummary(AxModel):
    """기록 `render.video.summary` (영상 요청 때부터, 승인 화면용)."""

    model: str
    seconds: int
    seconds_clamped_from: int | None = None
    aspect: str
    reference: Literal["character_original", "storyboard_image"]
    credits_estimate: float | None = None
