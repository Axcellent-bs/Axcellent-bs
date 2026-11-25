"""실사·가상 인물 시트 설명이 "그림체로 바꿔"로 나오던 것 (11/26 E).

운영 증거 11/25 CR-4dc50bc2cc75: virtual_human인데 "실사 사진으로 보여 그림체 캐릭터로 바꿔 외형을
정리한다". 원본 그대로 참조로 쓰는 종류(virtual_human·illustration)는 읽기 지시문이 "원본 그대로의
외형"이고, 결과 글에서 그림체 문장을 코드로 뺀다. real_photo는 지금 규칙. LLM을 부르지 않는다.
"""

from __future__ import annotations

import asyncio

from marketing_agent import character_sheet as cs

BAD = "실사 사진으로 보여 그림체 캐릭터로 바꿔 외형을 정리한다."


def _read(**over: object) -> cs.CharacterRead:
    base: dict = {
        "has_character": True, "real_person_photo": True,
        "summary_ko": f"갈색 단발머리에 노란 후드티를 입은 여성. {BAD}",
        "personality_ko": "밝다", "face_en": "oval face", "hair_en": "brown bob",
        "eyes_en": "dark eyes", "body_en": "slim", "outfit_en": "yellow hoodie",
        "art_style_en": "clean anime-style illustration", "proportions_en": "7 heads tall",
        "palette": [{"name": "brown", "hex": "#7B4A2D", "use": "머리"},
                    {"name": "yellow", "hex": "#E3B23C", "use": "후드티"},
                    {"name": "navy", "hex": "#1F2A44", "use": "바지"}],
        "poses_en": ["waving"], "seen_ko": ["얼굴", "그림체로 바꾼 상반신"],
        "guessed_ko": ["뒷모습", "실제 사람 사진이라 그림체로 변환함"],
    }  # fmt: skip
    base.update(over)
    return cs.CharacterRead.model_validate(base)


def _all_text(out) -> str:  # noqa: ANN001
    return " ".join([out.summary_ko, out.personality_ko or "", *out.seen_ko, *out.guessed_ko])


def test_virtual_human_has_no_stylize_sentence() -> None:
    out = cs.build_output(_read(), None, "virtual_human")
    text = _all_text(out)
    assert "그림체" not in text, text
    assert out.summary_ko.startswith("갈색 단발머리")
    assert out.stylized_from_photo is False and out.kind == "virtual_human"
    assert out.guessed_ko == ["뒷모습"] and out.seen_ko == ["얼굴"]


def test_illustration_has_no_stylize_sentence() -> None:
    out = cs.build_output(_read(real_person_photo=False), None, "illustration")
    assert "그림체" not in _all_text(out) and out.kind == "illustration"


def test_summary_only_stylize_gets_neutral_line() -> None:
    out = cs.build_output(_read(summary_ko=BAD), None, "virtual_human")
    assert out.summary_ko == cs.KEEP_SUMMARY


def test_real_photo_keeps_current_rule() -> None:
    out = cs.build_output(_read(), None, "real_photo")
    assert out.stylized_from_photo is True
    assert BAD.rstrip(".") in out.summary_ko  # LLM 글은 그대로
    assert "실제 사람 사진이라 얼굴을 옮기지 않고 그림체 캐릭터로 바꿈" in out.guessed_ko
    # "그림"으로 골랐어도 실제 사람 사진이면 real_photo 규칙 그대로
    still = cs.build_output(_read(), None, "illustration")
    assert still.kind == "real_photo" and still.stylized_from_photo is True
    assert "그림체 캐릭터로 바꿈" in " ".join(still.guessed_ko)


def test_prompt_by_kind() -> None:
    for kind in ("virtual_human", "illustration"):
        p = cs.prompt_for(kind)
        assert "원본 그대로의 외형" in p and "그림체 캐릭터(art_style_en)로 바꾼 외형" not in p
        assert "real_person_photo=true" in p
    assert cs.prompt_for("real_photo") == cs.PROMPT
    assert "그림체 캐릭터(art_style_en)로 바꾼 외형" in cs.PROMPT


def test_llm_gets_reference_prompt_for_virtual_human() -> None:
    seen: list[str] = []

    class LLM:
        async def structured(self, prompt, schema, untrusted=None, images=None,  # noqa: ANN001
                             context_id=None):  # noqa: ANN001, ANN201
            seen.append(prompt)
            return _read()

    import base64

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64  # 기존 시험과 같은 작은 PNG 머리
    image = {"media_type": "image/png", "data_base64": base64.b64encode(png).decode()}
    out = asyncio.run(cs.make_character_sheet({"image": image}, lambda: LLM(),
                                              kind="virtual_human"))  # fmt: skip
    assert "원본 그대로의 외형" in seen[0]
    assert "그림체" not in _all_text(out)
