"""스토리보드 이미지 프롬프트 (skill `storyboard_prompt`, read). 가짜 LLM으로만 돈다."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ax_agent_template.llm import LLMFormatError, MockLLMClient
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from ax_contracts.schemas.marketing_storyboard import CAMERA_ANGLES, SHOT_SIZES
from marketing_agent import handlers
from marketing_agent import storyboard as sb

CHAR = "The exact same character in every panel: short brown bob hair, cream cardigan."
STORY = "가을 들판에서 자전거를 타던 소녀가 고양이를 만나 노을을 보며 주먹밥을 나눠 먹는다"
SIZES = [s for s, _, _ in SHOT_SIZES]


def _shot(i: int, **over: object) -> dict:
    base = {
        "scene_ko": f"장면 {i}",
        "shot_size": SIZES[i % len(SIZES)],
        "angle": "eye_level",
        "composition_ko": f"배치 {i}",
        "has_character": i != 1,
        "subject_en": f"the girl doing thing {i}",
        "setting_en": "golden rice field",
        "light_en": "late afternoon sun",
        "mood_en": "nostalgic",
    }
    base.update(over)
    return base


def _read(n: int = 12, **over: object) -> sb.StoryRead:
    base: dict = {
        "title_ko": "가을 들판",
        "logline_ko": "소녀와 고양이의 하루",
        "shots": [_shot(i) for i in range(1, n + 1)],
    }
    base.update(over)
    return sb.StoryRead.model_validate(base)


def _make(untrusted: dict, llm: MockLLMClient | None):
    return asyncio.run(sb.make_storyboard(untrusted, lambda: llm))


def _llm(read: sb.StoryRead) -> MockLLMClient:
    return MockLLMClient(responses={sb.StoryRead: read})


def test_twelve_numbered_shots_with_composition_from_code_tables() -> None:
    out = _make({"story": STORY, "character_en": CHAR}, _llm(_read()))
    assert [s.no for s in out.shots] == list(range(1, 13))
    names = {sid: ko for sid, ko, _ in SHOT_SIZES}
    angles = {aid: ko for aid, ko, _ in CAMERA_ANGLES}
    for s in out.shots:
        assert s.shot_size_ko == names[s.shot_size]
        assert s.angle_ko == angles[s.angle]
    assert out.columns == 3 and out.character_used
    assert "12 numbered panels" in out.sheet_prompt_en and "4 rows" in out.sheet_prompt_en
    assert "Panel 12:" in out.sheet_prompt_en


def test_character_sentence_only_goes_into_shots_with_the_character() -> None:
    out = _make({"story": STORY, "character_en": CHAR}, _llm(_read()))
    landscape = out.shots[0]
    assert landscape.has_character is False and CHAR not in landscape.prompt_en
    assert CHAR in out.shots[1].prompt_en


def test_unknown_shot_and_angle_ids_fall_back_instead_of_failing() -> None:
    shots = [
        _shot(i, shot_size="Close-Up" if i == 2 else "drone", angle="worm") for i in range(1, 7)
    ]
    out = _make({"story": STORY, "shots": 6}, _llm(_read(shots=shots)))
    assert out.shots[1].shot_size == "close_up"  # 대소문자·하이픈은 맞춘다
    assert out.shots[0].shot_size == "full" and out.shots[0].angle == "eye_level"


def test_extra_shots_are_cut_and_missing_shots_are_refused() -> None:
    assert len(_make({"story": STORY, "shots": 6}, _llm(_read(9))).shots) == 6
    with pytest.raises(sb.StoryboardRefused) as err:
        _make({"story": STORY, "shots": 9}, _llm(_read(5)))
    assert err.value.code == "llm_failed"


def test_without_character_sentence_the_sheet_has_no_recurring_character() -> None:
    out = _make({"story": STORY, "shots": 6}, _llm(_read(6)))
    assert not out.character_used and "Recurring character" not in out.sheet_prompt_en
    assert out.style_en == sb.DEFAULT_STYLE


@pytest.mark.parametrize(
    ("untrusted", "code"),
    [
        ({}, "no_story"),
        ({"story": "   "}, "no_story"),
        ({"story": STORY, "shots": 7}, "invalid_input"),
        ({"story": STORY, "shots": True}, "invalid_input"),
    ],
)
def test_bad_inputs_are_refused_before_the_llm(untrusted: dict, code: str) -> None:
    with pytest.raises(sb.StoryboardRefused) as err:
        _make(untrusted, None)
    assert err.value.code == code


def test_no_llm_is_refused_not_invented() -> None:
    with pytest.raises(sb.StoryboardRefused) as err:
        _make({"story": STORY}, None)
    assert err.value.code == "llm_not_configured"


def test_llm_format_error_is_llm_failed() -> None:
    class Broken:
        async def structured(self, *a: object, **k: object) -> None:
            raise LLMFormatError("bad")

    with pytest.raises(sb.StoryboardRefused) as err:
        asyncio.run(sb.make_storyboard({"story": STORY}, lambda: Broken()))
    assert err.value.code == "llm_failed"


def _request(untrusted: dict) -> AgentRequest:
    return AgentRequest(
        meta=RequestMeta(
            context_id="ctx_test_storyboard",
            token="t",
            idempotency_key="k1",
            deadline=datetime.now(UTC) + timedelta(seconds=60),
        ),
        skill="storyboard_prompt",
        input={},
        untrusted=untrusted,
    )


def test_handler_returns_the_storyboard(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = _llm(_read())
    monkeypatch.setattr(handlers, "STORYBOARD_LLM", lambda: llm)
    result = asyncio.run(handlers.HANDLERS["storyboard_prompt"](_request({"story": STORY})))
    assert result.status == TaskStatus.COMPLETED
    assert len(result.output["shots"]) == 12 and result.output["sheet_prompt_en"]


def test_handler_refusal_is_a_failed_result_with_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handlers, "STORYBOARD_LLM", lambda: None)
    result = asyncio.run(handlers.HANDLERS["storyboard_prompt"](_request({"story": STORY})))
    assert result.status == TaskStatus.FAILED and result.outcome == "llm_not_configured"
