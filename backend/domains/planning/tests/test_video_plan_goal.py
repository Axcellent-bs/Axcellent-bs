"""영상 목적을 분명히 (11/26 K, 재현 님 "홍보인지 재미인지 광고인지 모르겠다").

목적 4가지(재미·정보·홍보·광고)를 코드가 정하고(LLM 아님), 제작안 goal·끝 행동(cta_ko)을 채운다.
가짜 LLM만 쓴다."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from planning_agent import video_plan as vp

pytestmark = pytest.mark.role("planning")


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


V = _load("video_plan_helpers_goal", "test_video_plan.py")
PROP42 = (
    "캣북스 겨울 독서 홍보 숏폼 영상 만들어줘. 15초 세로 영상이고 회사 캐릭터가 저녁에 거실 소파에 "
    "앉아 책을 고르다가, 탁자에서 책을 펼쳐 읽고, 눈 오는 바깥 서점 앞에서 손을 흔드는 흐름으로 "
    "해줘. 끝에 캣북스 채널 이름 넣어줘."
)


@pytest.mark.parametrize(
    ("request_text", "goal", "why"),
    [
        (PROP42, vp.PROMO, "의뢰문 '홍보'"),
        ("그냥 웃긴 릴스 하나 만들어줘", vp.FUN, "의뢰문 '웃긴'"),
        ("겨울에 읽기 좋은 책 3권 추천 영상", vp.INFO, "의뢰문 '추천'"),
        ("겨울 할인 이벤트 영상", vp.AD, "의뢰문 '할인'"),
        ("15초 세로 영상 만들어줘", vp.FUN, "낱말 없음"),
    ],
)  # fmt: skip
def test_goal_from_request_words(request_text: str, goal: str, why: str) -> None:
    assert vp.goal_why(V._prop(request_text=request_text)) == (goal, why)  # noqa: SLF001


def test_order_is_ad_info_promo_fun_and_request_comes_first() -> None:
    assert vp.goal_words("할인 꿀팁 홍보 밈") == (vp.AD, "할인")
    assert vp.goal_words("추천 홍보") == (vp.INFO, "추천")
    # 의뢰문 낱말이 제안 글 낱말보다 먼저
    p = V._prop(request_text="겨울 독서 홍보", idea="겨울 책 추천")  # noqa: SLF001
    assert vp.goal_why(p) == (vp.PROMO, "의뢰문 '홍보'")
    assert vp.goal_why(V._prop(idea="겨울 책 추천 숏츠")) == (vp.INFO, "제안 글 '추천'")  # noqa: SLF001


def test_a_goal_the_human_chose_comes_first() -> None:
    human = vp.rule_plan(V._prop()).model_copy(update={"written_by": "human", "goal": vp.INFO})  # noqa: SLF001
    p = V._prop(request_text="그냥 웃긴 릴스", video_plan=human)  # noqa: SLF001
    assert vp.goal_why(p) == (vp.INFO, "사람이 고름")


def test_goal_fills_the_old_fields() -> None:
    def promo(text: str) -> tuple[str, str]:
        return vp.goal_promo(V._prop(request_text=text))  # noqa: SLF001

    assert promo("그냥 웃긴 릴스") == (vp.FUN_FIRST, vp.END_CARD)
    assert promo("책 3권 추천") == (vp.PRODUCT, vp.CAPTION)
    # 홍보: promo_style은 지금 규칙(데이터·회사 설정, 없으면 fun_first), 브랜드는 소품
    assert promo(PROP42) == (vp.FUN_FIRST, vp.PROP)
    p = V._prop(request_text="캣북스 신간 홍보", promo_style="product")  # noqa: SLF001
    assert vp.goal_promo(p) == (vp.PRODUCT, vp.PROP)
    # 의뢰문 낱말이 없으면 데이터·회사 설정 그대로 (지금 규칙)
    p = V._prop(promo_style="product", brand_exposure="caption")  # noqa: SLF001
    assert vp.goal_promo(p) == (vp.PRODUCT, vp.CAPTION) and vp.goal_of(p) == vp.PROMO


def test_promo_request_gets_goal_and_cta(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = V._mock(V._llm_out())  # noqa: SLF001 - cta_ko를 비운 답
    plan = asyncio.run(vp.write(llm, V._prop(request_text=PROP42), "ctx"))  # noqa: SLF001
    assert plan.goal == vp.PROMO and plan.purpose == vp.CHANNEL_CONTENT
    assert plan.cta_ko == vp.GOAL_CTA[vp.PROMO] and plan.offer_ko == ""
    sent = V.plan_calls(llm)[0]
    assert vp.GOAL_DIRECTION[vp.PROMO].strip() in sent and vp.GOAL_CTA_RULE.strip() in sent
    assert len(V.plan_calls(llm)) == 1  # LLM 호출 수는 그대로


def test_llm_cta_is_kept_and_bad_cta_falls_back_without_dropping_the_plan() -> None:
    out = V._llm_out().model_copy(update={"cta_ko": "캣북스에서 겨울 책 더 보기"})  # noqa: SLF001
    plan = asyncio.run(vp.write(V._mock(out), V._prop(request_text=PROP42), "ctx"))  # noqa: SLF001
    assert plan.written_by == "llm" and plan.cta_ko == "캣북스에서 겨울 책 더 보기"
    bad = V._llm_out().model_copy(update={"cta_ko": "팔로우하면 1000권 선물"})  # noqa: SLF001
    plan = asyncio.run(vp.write(V._mock(bad), V._prop(request_text=PROP42), "ctx"))  # noqa: SLF001
    assert plan.written_by == "llm" and plan.cta_ko == vp.GOAL_CTA[vp.PROMO]


def test_info_asks_for_two_or_three_tip_scenes_without_invented_facts() -> None:
    llm = V._mock(V._llm_out())  # noqa: SLF001
    p = V._prop(request_text="겨울에 읽기 좋은 책 3권 추천")  # noqa: SLF001
    plan = asyncio.run(vp.write(llm, p, "ctx"))
    sent = V.plan_calls(llm)[0]
    assert "팁·추천 장면 2~3개" in sent and "근거에 없는 숫자·사실을 지어내지 않는다" in sent
    assert plan.goal == vp.INFO and plan.cta_ko


def test_offer_ad_on_a_virtual_brand_warns_but_still_makes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vp, "virtual_brand", lambda: True)
    out = V._llm_out().model_copy(update={"offer_ko": "겨울 신간", "cta_ko": "지금 만나 보세요"})  # noqa: SLF001
    plan = asyncio.run(vp.write(V._mock(out), V._prop(request_text="겨울 할인 이벤트 영상"), "ctx"))  # noqa: SLF001
    assert plan.goal == vp.AD and plan.purpose == vp.CLIENT_OFFER and plan.written_by == "llm"
    md = vp.markdown(plan)
    assert vp.VIRTUAL_AD_WARNING in md and "혜택 → 쓰는 장면 → 행동 문구" in md
    monkeypatch.setattr(vp, "virtual_brand", lambda: False)
    assert vp.VIRTUAL_AD_WARNING not in vp.markdown(plan)


def test_own_company_ad_is_the_b2b_solution_ad(monkeypatch: pytest.MonkeyPatch) -> None:
    p = V._prop(request_text="광고 영상 만들어줘")  # noqa: SLF001
    assert vp.purpose_of(p) == vp.CLIENT_OFFER  # 고객사 광고 = 혜택 광고
    monkeypatch.setattr(vp, "own_company", lambda: True)
    assert vp.purpose_of(p) == vp.SOLUTION_AD
    plan = vp.rule_plan(V._prop(request_text="AXcellent 솔루션 소개 광고"))  # noqa: SLF001
    assert plan.goal == vp.AD
    md = vp.markdown(plan.model_copy(update={"purpose": vp.SOLUTION_AD}))
    assert vp.AD_B2B_FLOW in md and vp.AD_B2B_SUCCESS in md


def test_markdown_goal_block_and_old_plans_without_goal() -> None:
    plan = vp.rule_plan(V._prop(request_text=PROP42))  # noqa: SLF001
    md = vp.markdown(plan)
    head = md.split("**콘셉트**")[0]  # 맨 위
    assert "**영상 목적**: 홍보 · 흐름: 브랜드·상품을 소품·자막으로 → 채널 이름" in head
    assert f"- 끝 행동: {plan.cta_ko}" in head and "- 성공 기준: 저장·팔로우" in head
    old = plan.model_copy(update={"goal": None})
    assert "**영상 목적**" not in vp.markdown(old)
