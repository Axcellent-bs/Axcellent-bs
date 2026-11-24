"""제작안 소재에 보고서 이름("트렌드 분석")이 들어가지 않는다 (11/24 재현 님, PROP-26).

데이터 보고서 기획 입력의 제목은 "트렌드 분석: {상품}" 또는 "트렌드 분석 의뢰"다.
이 제목이 주제·자막·할 일에 들어가 "트렌드 분석 키워드를 …" 같은 제작안이 나왔다.
소재는 인사이트 결론에서 잡는다. 의뢰 문장(고객 글)은 LLM에 넘기지 않는다(PROP-20 그대로).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas.planning import ReceiveInsightInput
from ax_workflow.models import Proposal
from planning_agent import proposals
from planning_agent import video_plan as vp

pytestmark = pytest.mark.role("planning")

ASK = "10~30대 여성이 보고 웃을 만한 공감 상황극으로 홍보 릴스 15초짜리 만들어 주세요"


def _prop(**kw) -> Proposal:
    base = {
        "id": "PROP-26", "title": "트렌드 분석 의뢰", "from": "data",
        "skill": "trend_report_insight",
        "insight": "#책추천 챌린지 형식이 20대 여성에게 반응이 좋다", "idea": "트렌드 분석 의뢰",
        "why": "인스타 조회 중앙값 1.2만", "created_at": datetime(2026, 11, 24, tzinfo=UTC),
    }  # fmt: skip
    return Proposal.model_validate({**base, **kw})


@pytest.mark.parametrize(
    ("title", "want"),
    [("트렌드 분석 의뢰", ""), ("트렌드 분석: 겨울 동화책", "겨울 동화책"),
     ("트렌드분석 : 겨울 동화책", "겨울 동화책"), ("가을 밤 독서 숏츠", "가을 밤 독서 숏츠")],
)  # fmt: skip
def test_subject_title_drops_report_name(title: str, want: str) -> None:
    assert vp.subject_title(title) == want


INSIGHT = "#책추천 챌린지 형식이 20대 여성에게 반응이 좋다"


def test_subject_falls_back_to_insight() -> None:
    assert vp.subject_of(_prop()) == INSIGHT
    assert vp.idea_of(_prop()) == ""  # 예전 규칙 초안의 할 일(= 보고서 이름)도 소재가 아니다
    assert vp.subject_of(_prop(title="트렌드 분석: 겨울 동화책")) == "겨울 동화책"


@pytest.mark.parametrize("promo", [(vp.PRODUCT, vp.END_CARD), (vp.FUN_FIRST, vp.END_CARD)])
def test_rule_plan_never_uses_report_name(promo) -> None:  # noqa: ANN001
    plan = vp.rule_plan(_prop(idea="공감 상황극 릴스"), promo)
    text = " ".join([plan.concept, plan.script, *(f"{s.visual} {s.caption} {s.prompt}"
                                                  for s in plan.scenes)])  # fmt: skip
    assert vp.REPORT_WORD not in text
    assert vp.topic_of(_prop())["topic"] == INSIGHT


def _llm(concept: str, caption: str) -> MockLLMClient:
    scenes = [{"visual": f"장면 {i}", "caption": caption if i == 0 else "",
               "prompt": "A mascot laughing at a cozy desk, slow dolly in"}
              for i in range(4)]  # fmt: skip
    out = vp._PlanOut.model_validate({"concept": concept, "script": "훅 → 공감 → 마무리",
                                      "scenes": scenes})  # fmt: skip
    return MockLLMClient(responses={vp._PlanOut: out})


def test_llm_gets_no_report_name_and_is_rejected_if_it_uses_it() -> None:
    good = _llm("공감 상황극 릴스", "이거 나만 그래?")
    plan = asyncio.run(vp.write(good, _prop(), "ctx", (vp.FUN_FIRST, vp.END_CARD)))
    assert plan.written_by == "llm"
    sent = good.calls[0]
    data = sent.split("<untrusted_data", 1)[-1]
    assert "트렌드 분석" not in data and INSIGHT in data
    bad = _llm("트렌드 분석 키워드를 상황극으로", "트렌드 분석, 이걸 고양이가?")
    plan = asyncio.run(vp.write(bad, _prop(), "ctx", (vp.FUN_FIRST, vp.END_CARD)))
    assert plan.written_by == "rule"  # 보고서 이름을 소재로 쓰면 규칙 제작안으로


def test_rule_draft_idea_is_the_insight_not_the_report_name() -> None:
    inp = ReceiveInsightInput.model_validate({
        "insight_id": "ins-1", "title": "트렌드 분석 의뢰", "summary": "#책추천 챌린지가 뜬다",
        "skill": "trend_report_insight", "request_text": ASK,
        "facts": [{"label": "조회 중앙값", "value": "1.2만", "source": "instagram"}],
    })  # fmt: skip
    assert proposals.rule_draft(inp).idea == "#책추천 챌린지가 뜬다"
