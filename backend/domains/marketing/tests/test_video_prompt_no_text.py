"""영상 프롬프트: 글자 요청 빼기 + 장면 시각 × 배속 + 참조 방식에 맞는 첫 문장 (11/25).

예진 님 지적: 프롬프트에 자막·CTA 글자를 넣으면 영상 모델이 글자를 그리고, #1742가 같은 글을 코드로
또 얹어 두 번 나온다. 화면(`frontend/src/lib/higgsfieldWeb.test.ts`)이 같은 고정 예시로 같은 결과를
본다(글자 하나까지 같게).
"""

from __future__ import annotations

import hashlib
from typing import Any

from marketing_agent import creative_render as rr

STORY = (
    "Animate this storyboard as one continuous short film, panel by panel in order, "
    "keeping the same characters, outfits and art style."
)
PERSON = (
    "Make one continuous short film of the same person as the reference images, "
    "keeping face, hair and outfit consistent."
)
NO_TEXT = "No on-screen text, captions, subtitles, logos or signs; text is added later."

# 화면 시험과 같은 고정 예시
SHOTS: list[dict[str, Any]] = [
    {"no": 1, "start_s": 0, "end_s": 3, "scene_ko": "들판  위 소녀", "speaker_ko": "소녀",
     "line_ko": "안녕", "caption_ko": "가을"},
    {"no": 2, "start_s": 3, "end_s": 5, "scene_ko": "노을", "line_ko": "함께 봐요"},
    {"no": 3, "start_s": None, "scene_ko": "x"},
]  # fmt: skip
FIXED = (
    f"{PERSON} flat 2D Follow this script panel by panel: "
    "Scene 1 (0-3.6s): 들판 위 소녀 / the action conveys: 안녕. "
    "Scene 2 (3.6-6s): 노을 / the action conveys: 함께 봐요. "
    f"{NO_TEXT}"
)


def _long() -> list[dict[str, Any]]:
    return [
        {"no": i, "start_s": i * 5, "end_s": i * 5 + 5, "scene_ko": "긴 장면 " * 30,
         "line_ko": "긴 대사 " * 40, "caption_ko": "자막" * 20}
        for i in range(1, 13)
    ]  # fmt: skip


def test_fixed_example_matches_the_screen() -> None:
    got = rr.storyboard_video_prompt("flat 2D", SHOTS, speed=1.2, reference="character_original")
    assert got == FIXED
    assert rr.NO_TEXT == NO_TEXT


def test_no_caption_no_cta_no_quotes_and_the_no_text_line() -> None:
    p = rr.storyboard_video_prompt("flat 2D", SHOTS)
    assert "가을" not in p and "on-screen text \"" not in p and "call to action" not in p
    assert '"' not in p  # 대사 따옴표 인용 없음 (입 모양·글자 유도 방지)
    assert "narration" not in p and "소녀:" not in p
    assert p.endswith(NO_TEXT)
    assert p.startswith(STORY)


def test_scene_times_are_multiplied_by_the_speed() -> None:
    assert "Scene 1 (0-3s)" in rr.storyboard_video_prompt(None, SHOTS)
    sped = rr.storyboard_video_prompt(None, SHOTS, speed=1.2)
    assert "Scene 1 (0-3.6s)" in sped and "Scene 2 (3.6-6s)" in sped
    assert "Scene 2 (4.5-7.5s)" in rr.storyboard_video_prompt(None, SHOTS, speed=1.5)


def test_opening_follows_the_reference_kind() -> None:
    assert rr.storyboard_video_prompt("x").startswith(STORY)
    assert rr.storyboard_video_prompt("x", reference="storyboard_image").startswith(STORY)
    assert rr.storyboard_video_prompt("x", reference="character_original") == f"{PERSON} x"
    assert rr.storyboard_video_prompt(None, reference="unknown") == STORY  # 모르는 값은 지금 문장


def test_long_script_stays_within_the_limit_and_keeps_the_last_line() -> None:
    p = rr.storyboard_video_prompt("soft anime " * 15, _long(), speed=1.2)
    assert len(p) <= rr.VIDEO_PROMPT_MAX
    assert p.endswith(NO_TEXT)
    assert "Scene 12 (72-78s)" in p
    # 화면 시험과 같은 값 (길이·해시)
    assert (len(p), hashlib.sha256(p.encode()).hexdigest()[:16]) == (1991, "22627dd87292ec68")


def test_mcp_params_use_the_chosen_reference_and_the_recorded_speed() -> None:
    rec: dict[str, Any] = {
        "storyboard": {"style_en": "flat 2D", "script_used": True, "shots": SHOTS},
        "render": {"video": {"summary": {"speed": 1.2}}},
    }
    params = rr._video_mcp_params(rec, 18)  # noqa: SLF001
    assert params["prompt"].startswith(STORY)  # 원본 참조가 아니면 스토리보드 그림
    assert "Scene 1 (0-3.6s)" in params["prompt"]
    # 요청할 때(기록에 아직 배속이 없을 때)는 넘긴 배속을 쓴다
    fresh = {"storyboard": rec["storyboard"]}
    assert "Scene 1 (0-4.5s)" in rr._video_mcp_params(fresh, 18, None, 1.5)["prompt"]  # noqa: SLF001
    assert rr.video_speed(fresh) == 1.0
    assert rr.video_speed({"render": {"video": {"speed": 1.5}}}) == 1.5
