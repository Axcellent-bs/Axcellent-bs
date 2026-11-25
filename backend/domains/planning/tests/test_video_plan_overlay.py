"""제작안 자막이 마케팅 코드가 얹는 글과 겹치지 않는다 (11/25).

마케팅(creative_overlay·caption_overlay)이 영상에 따로 얹는 것:
- 장면 자막(caption)을 화면 아래 22%에
- 마지막 2초 엔드카드(회사 이름 + cta_ko)를 같은 자리에
- 재생 배속(#1760)과 배경음악
그래서 기획은 마지막 장면 자막을 비우고, 회사 이름·cta_ko를 자막에 되풀이하지 않고,
LLM에게 배속·자막·엔드카드 글을 영상 프롬프트에 쓰지 말라고 알린다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas.planning import VideoScene
from ax_workflow.models import Proposal
from planning_agent import video_plan as vp

pytestmark = pytest.mark.role("planning")


def _prop(**kw) -> Proposal:  # noqa: ANN003
    base = {
        "id": "PROP-90", "title": "출근길 상황극", "from": "data", "skill": "trend_report_insight",
        "insight": "30대 여성이 출근길 상황극을 오래 본다", "idea": "출근길 공감 상황극",
        "why": "상황극 쇼츠 조회", "created_at": datetime(2026, 11, 25, tzinfo=UTC),
        "products": ["겨울 문고본"],
    }  # fmt: skip
    return Proposal.model_validate({**base, **kw})


def _scene(n: int, caption: str) -> VideoScene:
    return VideoScene(n=n, start_s=(n - 1) * 3, end_s=n * 3, visual=f"장면 {n}", caption=caption,
                      prompt="A woman on a subway, natural light")  # fmt: skip


def test_overlay_safe_clears_last_and_duplicates() -> None:
    scenes = [_scene(1, "나만 이래?"), _scene(2, "캣 북스"), _scene(3, "지금 만나 보세요!"),
              _scene(4, "출근길 한 권")]  # fmt: skip
    got = vp.overlay_safe(scenes, "캣북스", "지금 만나 보세요")
    assert [s.caption for s in got] == ["나만 이래?", "", "", ""]
    assert vp.overlay_safe([], "캣북스") == []


@pytest.mark.parametrize(
    "promo",
    [(vp.FUN_FIRST, vp.END_CARD), (vp.FUN_FIRST, vp.CAPTION), (vp.FUN_FIRST, vp.PROP),
     (vp.PRODUCT, vp.END_CARD)],
)  # fmt: skip
def test_rule_plans_leave_the_end_card_to_the_overlay(promo) -> None:  # noqa: ANN001
    plan = vp.rule_plan(_prop(), promo)
    assert plan.scenes[-1].caption == ""
    company = vp._company()["name"]
    if company:
        assert all(vp._bare(s.caption) != vp._bare(company) for s in plan.scenes)


def test_llm_is_told_what_the_overlay_adds_and_its_captions_are_cleaned() -> None:
    caps = ["나만 이래?", "자리는 없고", "10분이면 딴 세상", "출근길 한 권"]
    scenes = [{"visual": f"장면 {i}", "caption": c, "prompt": "A woman reading on a calm subway"}
              for i, c in enumerate(caps)]  # fmt: skip
    out = vp._PlanOut.model_validate({"concept": "출근길 상황극", "script": "훅 → 공감 → 반전",
                                      "scenes": scenes})  # fmt: skip
    llm = MockLLMClient(responses={vp._PlanOut: out})
    plan = asyncio.run(vp.write(llm, _prop(), "ctx", (vp.FUN_FIRST, vp.END_CARD)))
    sent = llm.calls[0]
    assert "마지막 2초 엔드카드" in sent and "배속" in sent
    assert "caption은 company.name" not in sent and "cta_ko와 같게" not in sent
    assert plan.written_by == "llm"
    assert plan.scenes[-1].caption == ""
    assert [s.caption for s in plan.scenes[:3]] == caps[:3]
