"""캐릭터 시트 이미지 프롬프트 (skill `character_sheet_prompt`, read). 가짜 LLM으로만 돈다."""

from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime, timedelta

import pytest

from ax_agent_template.llm import LLMFormatError, LLMResponseError, MockLLMClient
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from ax_contracts.schemas.marketing_character import (
    MAX_CHARACTER_IMAGE_BYTES,
    SHEET_EXPRESSIONS,
    SHEET_VIEWS,
)
from marketing_agent import character_sheet as cs
from marketing_agent import handlers

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
IMAGE = {"media_type": "image/png", "data_base64": base64.b64encode(PNG).decode()}


def _read(**over: object) -> cs.CharacterRead:
    base: dict = {
        "has_character": True,
        "real_person_photo": False,
        "summary_ko": "갈색 단발머리에 노란 후드티를 입은 밝은 소년",
        "personality_ko": "호기심 많고 밝다",
        "face_en": "round face, light warm skin tone",
        "hair_en": "short messy chestnut-brown hair with a cowlick",
        "eyes_en": "large amber eyes",
        "body_en": "slim build, teenage boy",
        "outfit_en": "mustard-yellow hoodie, dark navy jeans, white sneakers",
        "props_en": "small orange backpack",
        "art_style_en": "clean anime-style illustration",
        "proportions_en": "6 heads tall",
        "palette": [
            {"name": "chestnut brown", "hex": "#7B4A2D", "use": "머리"},
            {"name": "mustard yellow", "hex": "#E3B23C", "use": "후드티"},
            {"name": "navy", "hex": "#1F2A44", "use": "바지"},
        ],
        "poses_en": ["waving hello", "holding the backpack straps, walking"],
        "seen_ko": ["얼굴과 상반신", "노란 후드티"],
        "guessed_ko": ["뒷모습", "신발"],
    }
    base.update(over)
    return cs.CharacterRead.model_validate(base)


def _llm(read: cs.CharacterRead) -> MockLLMClient:
    return MockLLMClient(responses={cs.CharacterRead: read})


def _make(untrusted: dict, llm: MockLLMClient | None):
    return asyncio.run(cs.make_character_sheet(untrusted, lambda: llm))


def test_all_views_and_expressions_are_always_in_the_sheet() -> None:
    out = _make({"image": IMAGE}, _llm(_read()))
    views = [p.id for p in out.panels if p.kind == "view"]
    expressions = [p.id for p in out.panels if p.kind == "expression"]
    assert views == [v for v, _, _ in SHEET_VIEWS]  # 정면·뒷면·왼쪽·오른쪽·위·아래·3/4
    assert expressions == [e for e, _, _ in SHEET_EXPRESSIONS]  # 웃음·무표정·화남·놀람·슬픔
    for _, _, en in (*SHEET_VIEWS, *SHEET_EXPRESSIONS):
        assert en in out.prompt_en
    assert {p.kind for p in out.panels} >= {"pose", "palette", "details"}


def test_consistency_fixes_the_look_read_from_the_image() -> None:
    out = _make({"image": IMAGE}, _llm(_read()))
    for part in (
        "round face",
        "chestnut-brown hair",
        "amber eyes",
        "mustard-yellow hoodie",
        "#E3B23C",
    ):
        assert part in out.consistency_en
    assert out.consistency_en in out.prompt_en  # 프롬프트에 고정 문장이 그대로 들어간다
    assert "identical" in out.consistency_en


def test_seen_and_guessed_are_kept_apart() -> None:
    out = _make({"image": IMAGE}, _llm(_read()))
    assert out.seen_ko == ["얼굴과 상반신", "노란 후드티"]
    assert out.guessed_ko == ["뒷모습", "신발"]
    assert out.stylized_from_photo is False


def test_real_person_photo_becomes_a_stylized_character() -> None:
    out = _make({"image": IMAGE}, _llm(_read(real_person_photo=True)))
    assert out.stylized_from_photo is True
    assert "not photorealistic" in out.prompt_en
    assert "real person likeness" in out.negative_prompt_en
    assert any("그림체" in g for g in out.guessed_ko)


def test_note_goes_only_into_the_data_block_and_image_bytes_are_not_logged() -> None:
    llm = _llm(_read())
    out = _make({"image": IMAGE, "note": "  옷은   겨울옷으로 "}, llm)
    assert out.note_applied == "옷은 겨울옷으로"
    recorded = llm.calls[0]
    assert "옷은 겨울옷으로" in recorded  # 메모는 데이터 블록에 들어간다
    assert recorded.index("옷은 겨울옷으로") > recorded.index(cs.PROMPT.splitlines()[-1])
    assert IMAGE["data_base64"] not in recorded  # 이미지 바이트·base64는 남지 않는다
    assert len(llm.image_calls) == 1 and llm.image_calls[0][0].mime_type == "image/png"


@pytest.mark.parametrize(
    ("untrusted", "code"),
    [
        ({}, "no_image"),
        ({"image": "not-a-dict"}, "no_image"),
        (
            {"image": {"media_type": "image/gif", "data_base64": IMAGE["data_base64"]}},
            "invalid_image",
        ),
        (
            {"image": {"media_type": "image/png", "data_base64": "%%%not-base64%%%"}},
            "invalid_image",
        ),
        (
            {
                "image": {
                    "media_type": "image/png",
                    "data_base64": base64.b64encode(
                        b"x" * (MAX_CHARACTER_IMAGE_BYTES + 10)
                    ).decode(),
                }
            },
            "image_too_large",
        ),
    ],
)
def test_bad_images_are_refused_before_the_llm(untrusted: dict, code: str) -> None:
    llm = _llm(_read())
    with pytest.raises(cs.CharacterSheetRefused) as err:
        _make(untrusted, llm)
    assert err.value.code == code
    assert llm.calls == []


def test_no_llm_settings_refuses_instead_of_making_things_up() -> None:
    with pytest.raises(cs.CharacterSheetRefused) as err:
        _make({"image": IMAGE}, None)
    assert err.value.code == "llm_not_configured"


def test_no_character_in_the_image_is_refused() -> None:
    with pytest.raises(cs.CharacterSheetRefused) as err:
        _make({"image": IMAGE}, _llm(_read(has_character=False)))
    assert err.value.code == "no_character"


def _request(untrusted: dict) -> AgentRequest:
    return AgentRequest(
        meta=RequestMeta(
            context_id="ctx_test_charsheet",
            token="t",
            idempotency_key="k1",
            deadline=datetime.now(UTC) + timedelta(seconds=60),
        ),
        skill="character_sheet_prompt",
        input={},
        untrusted=untrusted,
    )


def test_handler_returns_the_sheet(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = _llm(_read())
    monkeypatch.setattr(handlers, "CHARACTER_SHEET_LLM", lambda: llm)
    result = asyncio.run(handlers.HANDLERS["character_sheet_prompt"](_request({"image": IMAGE})))
    assert result.status == TaskStatus.COMPLETED
    assert result.output["prompt_en"] and result.output["negative_prompt_en"]
    assert len([p for p in result.output["panels"] if p["kind"] == "view"]) == 7


def test_handler_refusal_is_a_failed_result_with_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handlers, "CHARACTER_SHEET_LLM", lambda: None)
    result = asyncio.run(handlers.HANDLERS["character_sheet_prompt"](_request({"image": IMAGE})))
    assert result.status == TaskStatus.FAILED
    assert result.outcome == "llm_not_configured"


def test_loose_llm_answer_is_cleaned_not_thrown_away() -> None:
    read = cs.CharacterRead.model_validate(
        {
            **_read().model_dump(),
            "palette": [
                {"name": "brown", "hex": "7b4a2d"},
                {"name": "yellow", "hex": "#fc0", "use": "후드티"},
                {"name": "navy", "hex": "#1F2A44"},
                {"name": "broken", "hex": "not-a-color"},
            ],
            "poses_en": ["a", "b", "c", "d", "e", "f"],
            "seen_ko": [f"본 것 {i}" for i in range(20)],
            "summary_ko": "가" * 500,
            "extra_field_from_model": "무시된다",
        }
    )
    out = _make({"image": IMAGE}, _llm(read))
    # 읽을 수 없는 색은 버린다
    assert [c.hex for c in out.palette] == ["#7B4A2D", "#FFCC00", "#1F2A44"]
    assert len([p for p in out.panels if p.kind == "pose"]) == 4
    assert len(out.seen_ko) == 12 and len(out.summary_ko) == 300


def test_too_few_readable_colors_is_refused_not_invented() -> None:
    read = cs.CharacterRead.model_validate(
        {
            **_read().model_dump(),
            "palette": [{"name": "x", "hex": "??"}, {"name": "y", "hex": "#123456"}],
        }
    )
    with pytest.raises(cs.CharacterSheetRefused) as err:
        _make({"image": IMAGE}, _llm(read))
    assert err.value.code == "llm_failed" and "팔레트" in err.value.message


class _Raises:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def structured(self, *args: object, **kwargs: object) -> object:
        raise self.exc


@pytest.mark.parametrize(
    ("exc", "words"),
    [
        (LLMResponseError("x", status_code=400), "이미지 입력"),
        (LLMResponseError("x", status_code=503), "503"),
        (LLMResponseError("x"), "시간 초과"),
        (LLMFormatError("x"), "형식"),
    ],
)
def test_llm_failures_say_which_kind(exc: Exception, words: str) -> None:
    with pytest.raises(cs.CharacterSheetRefused) as err:
        asyncio.run(cs.make_character_sheet({"image": IMAGE}, lambda: _Raises(exc)))
    assert err.value.code == "llm_failed" and words in err.value.message
