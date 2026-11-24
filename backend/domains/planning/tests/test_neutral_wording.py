"""기획 LLM 지시·화면 예시에 업종(서점·책·캣북스·쇼핑몰) 전제를 박지 않는다 (11/24 예진 님).

회사 이름·업종은 회사 설정에서 읽어 데이터 블록의 company로만 알린다. 없으면 업종 중립.
"""

from __future__ import annotations

import asyncio

import pytest

from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas.planning import ReceiveInsightInput
from planning_agent import proposals, spec_fill, video_plan
from planning_agent.domain import service

pytestmark = pytest.mark.role("planning")

BOOK_WORDS = ("온라인 서점", "서점", "캣북스", "쇼핑몰", "신간", "책을 얹어", "book covers")
PROMPTS = {
    "WRITE_PROMPT": proposals.WRITE_PROMPT,
    "FILL_PROMPT": spec_fill.FILL_PROMPT,
    "SUGGEST_PROMPT": spec_fill.SUGGEST_PROMPT,
    "video PROMPT": video_plan.PROMPT,
    "NEGATIVE": video_plan.NEGATIVE,
    "ANGLE_HINT": " ".join(proposals.ANGLE_HINT.values()),
}


@pytest.mark.parametrize("name", list(PROMPTS))
def test_llm_instructions_have_no_industry_premise(name: str) -> None:
    text = PROMPTS[name]
    assert not [w for w in BOOK_WORDS if w in text], name


def test_prompts_say_company_comes_from_data() -> None:
    for text in (proposals.WRITE_PROMPT, spec_fill.FILL_PROMPT, spec_fill.SUGGEST_PROMPT):
        assert "데이터의 company" in text and "업종을 짐작하지 않고" in text


def test_sample_texts_have_no_industry_premise() -> None:
    samples = [*sum(service.SAMPLE_TICKETS.values(), []), *sum(service.SIMPLE_TICKETS.values(), [])]
    samples += sum(service.DEPT_CHAT.values(), [])
    assert not [s for s in samples if any(w in s for w in BOOK_WORDS)]


def test_proposal_draft_sends_company_block(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        proposals, "company_info", lambda: {"name": "누비투어", "industry": "여행", "about": ""}
    )
    llm = MockLLMClient(
        responses={proposals._WriteOut: proposals._WriteOut(idea="할 일", why="이유")}
    )
    inp = ReceiveInsightInput.model_validate({
        "insight_id": "i-1", "title": "제목", "summary": "요약", "skill": "trend_history",
        "facts": [{"label": "검색", "value": "1.5배", "source": "trend"}],
    })  # fmt: skip
    asyncio.run(proposals.ProposalWriter(llm).draft(inp, "ctx"))
    sent = str(llm.calls[-1])
    assert "누비투어" in sent and "여행" in sent and "온라인 서점" not in sent
