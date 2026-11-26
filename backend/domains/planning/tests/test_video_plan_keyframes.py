"""영상 제작안 키프레임 칸·광고 목적 (11/24 멘토링: "기획이 아주 디테일해야 함", "광고는 광고답게").

기획은 장면마다 첫 화면·끝 화면·사이 동작·카메라를 글로만 쓴다(모델 모름). 광고 목적은 제안 글
낱말로 코드가 정하고, LLM은 그 목적의 장면 흐름대로 쓴다."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas.planning import VideoPlan, VideoScene
from ax_workflow.models import Proposal
from planning_agent import video_plan as vp

pytestmark = pytest.mark.role("planning")


def plan_call(llm) -> str:  # noqa: ANN001
    """마지막 제작안 호출 (11/25부터 SVG 콘티 호출이 뒤에 한 번 더 붙는다)."""
    return [c for c in llm.calls if "콘티 작가" not in c][-1]


PRODUCT = (vp.PRODUCT, vp.END_CARD)


def _prop(**kw) -> Proposal:
    base = {
        "id": "PROP-1", "title": "가을 밤 10분 독서 숏츠", "from": "data", "skill": "trend_history",
        "insight": "독서 검색이 11월에 오른다", "idea": "고양이 점원이 밤 10분 독서를 권하는 숏츠",
        "why": "독서 검색이 평균의 1.6배", "products": ["밤의 도서관"], "format": "숏폼 15초",
        "content_category": "브이로그", "angle": "with_product",
        "created_at": datetime(2026, 11, 19, tzinfo=UTC),
    }  # fmt: skip
    return Proposal.model_validate({**base, **kw})


def _scene(i: int, **kw) -> dict:
    return {
        "visual": f"장면 {i} 화면",
        "caption": f"자막 {i}",
        "prompt": "The cat mascot reads under a warm lamp, slow dolly in",
        "start_frame": f"고양이 한 마리가 가운데 책상에 앉아 왼쪽 창문을 본다 ({i})",
        "end_frame": f"고양이가 카메라 쪽으로 고개를 돌린다 ({i})",
        "action_en": "The cat slowly turns its head from the window toward the camera and blinks",
        "camera_en": "Medium shot, slow push-in",
        **kw,
    }


def _out(n: int = 4, offer: str = "", cta: str = "", **scene_kw) -> vp._PlanOut:
    return vp._PlanOut.model_validate(
        {
            "concept": "밤 독서 습관",
            "script": "훅 → 공감 → 상품 → 마무리",
            "scenes": [_scene(i, **scene_kw) for i in range(n)],
            "offer_ko": offer,
            "cta_ko": cta,
        }
    )


def _mock(out) -> MockLLMClient:
    return MockLLMClient(responses={vp._PlanOut: out})


# ---------------------------------------------------------------- 계약


def test_old_plan_json_without_new_fields_still_parses() -> None:
    old = {
        "concept": "c", "duration_s": 10, "script": "s", "style": "st", "negative": "ng",
        "scenes": [
            {"n": 1, "start_s": 0, "end_s": 4, "visual": "v1", "caption": "", "prompt": "p1"},
            {"n": 2, "start_s": 4, "end_s": 10, "visual": "v2", "prompt": "p2"},
        ],
    }  # fmt: skip
    plan = VideoPlan.model_validate(old)
    assert plan.purpose is None and plan.offer_ko == "" and plan.cta_ko == ""
    s = plan.scenes[0]
    assert (s.start_frame, s.end_frame, s.action_en, s.camera_en) == ("", "", "", "")


def test_contract_new_fields_have_limits() -> None:
    base = {"n": 1, "start_s": 0, "end_s": 2, "visual": "v", "prompt": "p"}
    with pytest.raises(ValueError):
        VideoScene.model_validate({**base, "start_frame": "가" * 201})
    with pytest.raises(ValueError):
        VideoScene.model_validate({**base, "camera_en": "a" * 121})
    plan = vp.rule_plan(_prop()).model_dump()
    with pytest.raises(ValueError):
        VideoPlan.model_validate({**plan, "purpose": "meme"})
    with pytest.raises(ValueError):
        VideoPlan.model_validate({**plan, "cta_ko": "가" * 41})


# ---------------------------------------------------------------- 광고 목적 (코드 규칙)


@pytest.mark.parametrize(
    ("kw", "want"),
    [
        (
            {"title": "우리 솔루션 소개 숏츠", "idea": "주문 자동화 솔루션을 보여 준다"},
            "solution_ad",
        ),
        ({"title": "겨울 특가 이벤트", "idea": "신간 할인 혜택을 알린다"}, "client_offer"),
        ({}, "channel_content"),
    ],
)
def test_purpose_from_proposal_words(kw: dict, want: str) -> None:
    assert vp.purpose_of(_prop(**kw)) == want


def test_offer_words_win_over_solution_words() -> None:
    assert vp.purpose_of(_prop(title="솔루션 도입 할인 이벤트")) == "client_offer"


def test_prompt_follows_purpose_structure() -> None:
    llm = _mock(_out(offer="신간 할인", cta="지금 만나 보세요"))
    asyncio.run(vp.write(llm, _prop(title="겨울 특가 이벤트"), "ctx"))
    sent = plan_call(llm)
    assert "고객 혜택 광고" in sent and "혜택 먼저(첫 3초)" in sent
    assert "재미 먼저, 브랜드는 끝에" not in sent  # 광고면 채널 콘텐츠 흐름을 쓰지 않는다
    llm = _mock(_out())
    asyncio.run(vp.write(llm, _prop(title="우리 솔루션 소개"), "ctx"))
    assert "문제(보는 사람이 겪는 불편) → 반전 → 해결 장면" in plan_call(llm)
    llm = _mock(_out())
    asyncio.run(vp.write(llm, _prop(), "ctx"))
    sent = plan_call(llm)
    assert "재미 먼저, 브랜드는 끝에" in sent and "- 광고 목적:" not in sent
    assert "offer_ko·cta_ko는 빈 문자열 (채널 콘텐츠)" in sent


def test_prompt_asks_for_keyframe_fields_and_rules() -> None:
    llm = _mock(_out())
    asyncio.run(vp.write(llm, _prop(), "ctx"))
    sent = plan_call(llm)
    for word in ("start_frame", "end_frame", "action_en", "camera_en", "offer_ko", "cta_ko"):
        assert word in sent
    assert "장면 i의 end_frame과 장면 i+1의 start_frame은 같은 순간" in sent
    assert "동작은 장면마다 하나만" in sent and "몇 명인지" in sent
    assert "3초 안에 시선을 끄는 훅" in sent


# ---------------------------------------------------------------- LLM 답의 새 칸


def test_llm_keyframe_fields_are_kept() -> None:
    plan = asyncio.run(vp.write(_mock(_out()), _prop(), "ctx"))
    assert plan.written_by == "llm" and plan.purpose == "channel_content"
    s = plan.scenes[0]
    assert (
        s.start_frame.startswith("고양이 한 마리가 가운데")
        and s.camera_en == "Medium shot, slow push-in"
    )
    assert s.action_en.startswith("The cat slowly turns")
    assert plan.offer_ko == "" and plan.cta_ko == ""


def test_channel_content_drops_offer_and_cta() -> None:
    plan = asyncio.run(vp.write(_mock(_out(offer="혜택", cta="지금 보기")), _prop(), "ctx"))
    assert plan.purpose == "channel_content" and plan.offer_ko == "" and plan.cta_ko == ""


def test_client_offer_keeps_offer_cta_and_two_second_hook() -> None:
    out = _out(offer="겨울 신간 할인", cta="지금 만나 보세요")
    plan = asyncio.run(vp.write(_mock(out), _prop(title="겨울 특가 이벤트"), "ctx", PRODUCT))
    assert plan.purpose == "client_offer"
    assert plan.offer_ko == "겨울 신간 할인" and plan.cta_ko == "지금 만나 보세요"
    assert plan.scenes[0].end_s == vp.HOOK_SECONDS  # 상품 홍보여도 광고면 첫 장면 2초 훅


def test_old_style_llm_answer_without_new_fields_still_works() -> None:
    old = vp._PlanOut.model_validate(
        {
            "concept": "밤 독서",
            "script": "흐름",
            "scenes": [{"visual": f"v{i}", "prompt": "A cat reads a book"} for i in range(4)],
        }
    )
    plan = asyncio.run(vp.write(_mock(old), _prop(), "ctx"))
    assert plan.written_by == "llm" and all(s.end_frame == "" for s in plan.scenes)


EMPTY_KEYFRAMES = ("", "", "", "")


def _keyframes(s: VideoScene) -> tuple[str, str, str, str]:
    return (s.start_frame, s.end_frame, s.action_en, s.camera_en)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("end_frame", "고양이가 가게 간판 앞에 선다", "text_request"),  # 글자
        ("action_en", "The cat writes a slogan on the wall", "text_request"),
        ("start_frame", "유명인 닮은 고양이가 서 있다", "real_person"),  # 실존 인물
        ("camera_en", "Close-up on a Starbucks cup", "real_brand"),  # 실제 브랜드
    ],
)
def test_forbidden_words_in_keyframe_fields_clear_only_that_scene(
    field: str, value: str, code: str
) -> None:
    # 11/25 바꾼 기대값: 예전엔 keyframe_rule로 제작안 전체를 버렸다 → 이제 그 장면 네 칸만 비운다
    out = _out()
    setattr(out.scenes[1], field, value)
    assert vp.reject_reason(_prop(), out) is None
    assert code in vp.keyframe_problems(out.scenes[1])
    plan = asyncio.run(vp.write(_mock(out), _prop(), "ctx"))
    assert plan is not None and plan.written_by == "llm"
    assert _keyframes(plan.scenes[1]) == EMPTY_KEYFRAMES
    kept = plan.scenes[1]
    assert (kept.visual, kept.caption) == ("장면 1 화면", "자막 1")
    assert kept.prompt == "The cat mascot reads under a warm lamp, slow dolly in"
    for i in (0, 2, 3):
        assert plan.scenes[i].action_en.startswith("The cat slowly turns")
        assert plan.scenes[i].camera_en == "Medium shot, slow push-in"


def test_instagram_in_one_action_keeps_the_plan_and_clears_that_scene(
    caplog: pytest.LogCaptureFixture,
) -> None:
    out = _out()
    out.scenes[2].action_en = "The cat scrolls instagram on a phone and smiles"
    with caplog.at_level("INFO"):
        plan = asyncio.run(vp.write(_mock(out), _prop(), "ctx"))
    assert plan is not None and plan.written_by == "llm" and len(plan.scenes) == 4
    assert _keyframes(plan.scenes[2]) == EMPTY_KEYFRAMES
    assert plan.scenes[2].visual == "장면 2 화면"
    assert all(_keyframes(plan.scenes[i]) != EMPTY_KEYFRAMES for i in (0, 1, 3))
    assert plan.scenes[3].start_frame.startswith("고양이 한 마리가 가운데")
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "키프레임 칸 비움" in logged and "instagram" not in logged  # 값 없이 이유 코드만


def test_forbidden_scene_prompt_still_falls_back_to_rule() -> None:
    out = _out()
    out.scenes[0].prompt = "Close-up on a Starbucks cup"
    assert vp.reject_reason(_prop(), out) == "prompt_rule"
    plan = asyncio.run(vp.write(_mock(out), _prop(), "ctx"))
    # 11/25 규칙 제작안 이유가 같이 남는다 (나머지 칸은 규칙 제작안 그대로).
    # 가짜 LLM에 포즈 답이 없어 콘티 LLM 단계는 실패 → 11/26 실패 사다리 4단 규칙 포즈
    # (예전 기대값: svg_reason "error"·svg_detail "KeyError", 장면 그림 없음)
    # 11/26 U: 어느 규칙인지 코드도 남는다 (Starbucks = real_brand)
    n = len(plan.scenes)
    want = {"fallback_reason": "rejected", "svg_reason": "",
            "fallback_detail": "prompt_rule:real_brand",
            "svg_detail": f"rule_pose_{n}"}  # fmt: skip
    assert all(s.start_svg and s.end_svg for s in plan.scenes)
    clean = {"start_pose": None, "end_pose": None, "start_svg": "", "end_svg": ""}
    bare = plan.model_copy(update={"scenes": [s.model_copy(update=clean) for s in plan.scenes]})
    assert bare == vp.rule_plan(_prop()).model_copy(update=want)


def test_topic_name_in_keyframe_is_rejected() -> None:
    facts = [{"label": "주제 '팬덤' 후보 별빛소년단 (언급 3회)", "value": "3", "source": "trend"}]
    p = _prop(facts=facts)
    out = _out()
    out.scenes[2].end_frame = "별빛소년단 응원봉을 든 고양이"
    assert vp.reject_reason(p, out) == "real_name_in_scene"


def test_offer_with_invented_number_or_marketing_word_is_rejected() -> None:
    p = _prop(title="겨울 특가 이벤트")
    assert vp.reject_reason(p, _out(offer="전 품목 50% 할인"), purpose="client_offer") == (
        "invented_offer_number"
    )
    assert vp.reject_reason(p, _out(offer="밤 10분 독서 혜택"), purpose="client_offer") is None
    assert vp.reject_reason(p, _out(cta="지금 팔로우"), purpose="client_offer") == "marketing_word"


# ---------------------------------------------------------------- 규칙 제작안


@pytest.mark.parametrize("promo", [PRODUCT, (vp.FUN_FIRST, vp.END_CARD)])
def test_rule_plan_start_frame_is_visual_and_rest_empty(promo) -> None:
    plan = vp.rule_plan(_prop(), promo)
    assert plan.purpose is None and plan.offer_ko == "" and plan.cta_ko == ""
    for s in plan.scenes:
        assert s.start_frame == s.visual
        assert (s.end_frame, s.action_en, s.camera_en) == ("", "", "")


# ---------------------------------------------------------------- 기획서 표


def test_markdown_shows_keyframes_and_purpose_only_when_present() -> None:
    rule_md = vp.markdown(vp.rule_plan(_prop()))
    assert "장면별 키프레임" not in rule_md and "광고 목적" not in rule_md
    out = _out(offer="겨울 신간 할인", cta="지금 만나 보세요")
    plan = asyncio.run(vp.write(_mock(out), _prop(title="겨울 특가 이벤트"), "ctx"))
    md = vp.markdown(plan)
    assert "**광고 목적**: 고객 혜택 광고" in md and "- 혜택: 겨울 신간 할인" in md
    assert "1. 고양이 한 마리가 가운데 책상에 앉아 왼쪽 창문을 본다 (0) → " in md
    assert "· Medium shot, slow push-in" in md
