"""마케팅 콘티 칸 = 3D 마네킹 (11/25 운영: 의뢰 스토리보드가 납작한 SVG로 나왔다).

콘티를 만드는 마케팅 길 둘(의뢰 흐름 `board_svg`, 스킬 `storyboard_prompt`·"대본대로 스토리보드 다시
만들기")도 기획 제작안과 같은 렌더러로 그린다. LLM은 칸마다 포즈 숫자(`KeyPose`)만 내고 그림은
공용 렌더러(`ax_agent_template.pose_render`)가 그린다. LLM에게 SVG 원문을 받지 않는다
(`storyboard.SVG_RULES`의 "단순 도형으로 그리기"는 더 쓰지 않는다).

- 포즈 지시문은 기획 `planning_agent.storyboard_svg.POSE_RULES`와 같은 숫자 규칙(`POSE_FIELDS`)이고,
  shot을 고르는 근거만 마케팅 칸 값(scene_ko·composition_ko·shot_size·angle·camera)이다
- 엔드카드 칸(마지막 칸, "엔드 카드"·"채널 이름" 칸)은 인물 없이 배경 그라데이션만 그린다
  (글자는 영상에 ffmpeg 엔드카드가 얹으니 SVG에 "채널 이름" 박스·글자를 그리지 않는다)
- 그린 SVG도 `svg_sanitize`를 한 번 통과시킨다. 포즈가 없거나 정리에 떨어진 칸은 빈 칸(배치판)
- `MARKETING_SVG_LLM_MODEL`이 있으면 콘티 호출(의뢰 흐름)만 그 모델(주소·키는 마케팅 설정 그대로)
"""

from __future__ import annotations

import os
from dataclasses import replace
from typing import Any, Final

from pydantic import BaseModel, ConfigDict

from ax_agent_template import company_ai, pose_ladder, pose_render
from ax_agent_template.llm import LLMClient
from ax_agent_template.pose_render import PoseLLM
from marketing_agent.svg_sanitize import sanitize_svg

ASPECT: Final = "9:16"
MODEL_ENV: Final = "MARKETING_SVG_LLM_MODEL"
# 엔드카드 칸을 알아보는 말 (기획 규칙 제작안 "마무리: 채널 이름 엔드 카드", 대본 끝 칸)
END_CARD_WORDS: Final = ("엔드 카드", "엔드카드", "채널 이름", "채널명", "end card", "endcard")

POSE_RULES = (
    """- 칸마다 pose에 마네킹 포즈 숫자만 JSON으로 쓴다.
  글 문단·SVG·그림 원문은 쓰지 않는다(그림은 코드가 포즈로 그린다).
  - shot: 칸의 shot_size·camera로 고른다. close_up·extreme_close_up·detail·close-up → "close",
    medium·over_shoulder·waist → "medium", extreme_wide·wide·full·full body → "full"
"""
    + pose_render.POSE_FIELDS
    + """- 인물 위치·시선·손 동작은 scene_ko·composition_ko·angle을 각도로 옮긴다.
- has_character가 false인 칸은 pose를 null로 둔다(인물을 그리지 않는다).
- 모든 칸을 같은 포즈로 두지 않는다. 칸마다 샷·방향·팔 동작이 달라 보이게 한다.
"""
)


class PanelPose(BaseModel):
    model_config = ConfigDict(extra="ignore")

    no: int = 0
    pose: PoseLLM | None = None


class BoardPoseRead(BaseModel):
    """의뢰 흐름 LLM 답. 칸 수가 어긋나도 맞는 번호만 쓴다."""

    model_config = ConfigDict(extra="ignore")

    panels: list[PanelPose] = []


def is_end_card(*texts: Any) -> bool:
    """칸 글에 엔드카드 말이 있으면 True (호출하는 쪽이 마지막 칸에만 묻는다)."""
    joined = " ".join(str(t or "") for t in texts).lower()
    return any(w in joined for w in END_CARD_WORDS)


def pose_svg(
    got: PoseLLM | None, label: str, at_s: float | None = None, text: str = ""
) -> str:
    """LLM 포즈 → 정리까지 마친 3D 마네킹 SVG. 포즈가 없거나 못 쓰면 빈 문자열.

    11/26 `at_s`(칸 시작 초)를 주면 라벨은 "N초 · 동작"(LLM이 적은 시각은 버림), `text`(칸 글)의
    마이크 같은 낱말은 소품으로 넣는다."""
    if got is None:
        return ""
    pose = pose_render.key_pose(got, label, at_s, text)
    return (sanitize_svg(pose_render.render(pose, ASPECT)) or "") if pose else ""


def end_card_svg() -> str:
    """엔드카드 칸: 배경 그라데이션만 (인물·글자 없음)."""
    return sanitize_svg(pose_render.backdrop(ASPECT)) or ""


def svg_llm() -> LLMClient | None:
    """콘티 포즈를 받을 마케팅 LLM. `MARKETING_SVG_LLM_MODEL`이 있으면 모델만 그것으로.

    회사 키가 있으면 회사 값, 없으면 `MARKETING_LLM_*`·`LLM_*`. 모델·주소가 없으면 None."""
    settings = company_ai.settings_for("marketing")
    model = os.getenv(MODEL_ENV, "").strip()
    if model:
        settings = replace(settings, model=model)
    if not (settings.model and settings.base_url):
        return None
    return LLMClient("marketing", settings)


def base_llm() -> LLMClient | None:
    """실패 사다리 3단(11/26): `MARKETING_SVG_LLM_MODEL`을 썼을 때만, env 없을 때와 같은 마케팅 기본
    모델 클라이언트. env가 없으면 None (1·2단이 이미 기본 모델)."""
    if not os.getenv(MODEL_ENV, "").strip():
        return None
    settings = company_ai.settings_for("marketing")
    if not (settings.model and settings.base_url):
        return None
    return LLMClient("marketing", settings)


def rule_svg(text: str, label: str, at_s: float | None = None) -> str:
    """실패 사다리 4단(11/26): LLM 없이 칸 글 낱말로 정한 포즈의 마네킹 SVG (공용 `pose_ladder`)."""
    return pose_svg(pose_ladder.rule_pose(text, label), label, at_s, text)


__all__ = [
    "MODEL_ENV",
    "POSE_RULES",
    "BoardPoseRead",
    "PanelPose",
    "base_llm",
    "end_card_svg",
    "rule_svg",
    "is_end_card",
    "pose_svg",
    "svg_llm",
]
