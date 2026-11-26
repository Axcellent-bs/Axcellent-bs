"""글자 요청 검사가 금지 문장("no text"·"글자 없이")을 글자 요청으로 보지 않는다 (11/26 A).

운영 wr_bc24dee53e(누비투어 제안 1): LLM이 안전하라고 넣은 "no text, no logo"에 걸려 규칙 초안
(fallback_detail prompt_rule:text_request)이 됐다. 실존 인물·브랜드·주소 검사는 그대로."""

from __future__ import annotations

import pytest

from planning_agent import video_plan as vp

pytestmark = pytest.mark.role("planning")


@pytest.mark.parametrize(
    "prompt",
    [
        "cinematic, no text, no logo",
        "A traveler at a hot spring, without text or logos, soft light",
        "no on-screen text, no captions, no subtitles, no watermark",
        "clean frame, text-free, logo free, avoid signage, free of typography",
        "zero words, no letters, no writing, no signs, no titles",
        "글자 없이 깔끔한 배경",
        "자막 없이, 로고 없는 화면, 문구 금지, 간판 넣지 않은 거리",
    ],
)
def test_negated_text_words_are_not_a_text_request(prompt: str) -> None:
    assert vp.prompt_problems(prompt) == []


@pytest.mark.parametrize(
    "prompt",
    [
        "a neon sign with the shop name",
        "자막: 안녕",
        "no logo, but a big title card in the middle",  # 금지 뒤에 글자를 그리라는 말이 남아 있다
        "the word HELLO written on the wall",
    ],
)
def test_real_text_requests_still_fail(prompt: str) -> None:
    assert "text_request" in vp.prompt_problems(prompt)


def test_other_rules_see_the_original_text() -> None:
    assert vp.prompt_problems("no text, a celebrity lookalike") == ["real_person"]
    assert vp.prompt_problems("no logo, Starbucks cup") == ["real_brand"]
    assert vp.prompt_problems("no text, visit www.example.com") == ["url_or_number"]


def test_keyframe_fields_use_the_same_check() -> None:
    scene = vp._SceneOut(visual="온천", prompt="cinematic, no text, no logo",  # noqa: SLF001
                         start_frame="글자 없이 깔끔한 온천", end_frame="자막 없이 웃는 여행자",
                         action_en="she smiles, no captions", camera_en="medium shot")  # fmt: skip
    assert vp.keyframe_problems(scene) == []
    bad = scene.model_copy(update={"start_frame": "간판에 가게 이름"})
    assert vp.keyframe_problems(bad) == ["text_request"]
