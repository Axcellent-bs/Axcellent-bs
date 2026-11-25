"""기획 제작안이 의뢰에서 고른 회사 캐릭터를 따른다 (11/26 wr_012c98d9d9: 실사 가상 인물을 골랐는데
제작안이 "고양이 귀를 단" 일러스트로 나왔다). 가짜 LLM·가짜 마케팅만 쓴다."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas.planning import ReceiveInsightOutput
from ax_workflow.models import CompanyCharacter
from planning_agent import proposals as ext
from planning_agent import video_plan as vp
from planning_agent.domain.service import PlanningService

pytestmark = pytest.mark.role("planning")


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


V = _load("video_plan_helpers_company_character", "test_video_plan.py")
LOOK = "a young woman with long straight black hair, blunt bangs and a plain white T-shirt"
VIRTUAL = CompanyCharacter(
    kind="virtual_human", look_en=LOOK, summary_ko="검은 긴 생머리·앞머리·흰 반팔", photo=True
)
DRAWN = CompanyCharacter(
    kind="illustration", look_en="a round orange cat mascot with a green scarf",
    summary_ko="초록 목도리 주황 고양이", photo=False,
)  # fmt: skip
RECORD = "CR-4dc50bc2cc75"


@dataclass
class SeqLLM(MockLLMClient):
    """제작안(_PlanOut) 답을 차례로 돌려준다 (다시 쓰기 시험)."""

    plans: list[Any] = field(default_factory=list)

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):
        if schema is vp._PlanOut and self.plans:  # noqa: SLF001
            self.responses[schema] = self.plans.pop(0)
        return await super().structured(prompt, schema, untrusted, images, context_id)


def _plan_calls(llm: MockLLMClient) -> list[str]:
    return V.plan_calls(llm)


# ---------------------------------------------------------------- 그림체·주인공


def test_virtual_human_is_photorealistic_and_not_an_animated_clip() -> None:
    p = V._prop(company_character=VIRTUAL)  # noqa: SLF001
    for plan in (vp.rule_plan(p, V.PRODUCT), vp.rule_plan(p, (vp.FUN_FIRST, vp.END_CARD))):
        assert plan.character_source == "company_character" and plan.character_look == "photo"
        assert plan.character_ko == "검은 긴 생머리·앞머리·흰 반팔"
        assert vp.PHOTO_STYLE_EN in plan.style and "animated clip" not in plan.style
        assert "long straight black hair" in plan.style
        assert all("long straight black hair" in s.prompt for s in plan.scenes[:3])
    brand = vp.tenant.brand()
    assert brand["setting_en"] in vp.rule_plan(p, V.PRODUCT).style  # 회사 브랜드는 배경에만
    assert brand["character_en"] not in vp.rule_plan(p, V.PRODUCT).style


def test_illustration_keeps_the_sheet_drawing_style() -> None:
    plan = vp.rule_plan(V._prop(company_character=DRAWN), V.PRODUCT)  # noqa: SLF001
    assert plan.character_look == "illustration"
    assert vp.ILLUSTRATION_STYLE_EN in plan.style and "animated clip" in plan.style
    assert "orange cat mascot" in plan.style


def test_without_a_company_character_the_plan_is_exactly_the_same() -> None:
    p = V._prop()  # noqa: SLF001
    plan = vp.rule_plan(p, V.PRODUCT)
    assert plan.character_source == "brand" and plan.character_ko == ""
    b = vp.tenant.brand()
    want = (
        f"Short vertical 9:16 animated clip, {b['style_en']}, {b['character_en']} as the main "
        f"character, {b['setting_en']}, gentle camera movement"
    )
    assert plan.style == vp._short(want, vp.STYLE_MAX)  # noqa: SLF001
    assert vp.cast(p) == b
    # LLM 지시문도 지금과 글자 그대로 (회사 캐릭터 줄이 없다)
    llm = V._mock(V._llm_out())  # noqa: SLF001
    asyncio.run(vp.write(llm, p, "ctx", V.PRODUCT))
    sent = _plan_calls(llm)
    assert len(sent) == 1 and vp.CHARACTER_RULE.strip() not in sent[0]
    assert "main_character" not in sent[0]


def test_llm_prompt_gets_the_character_as_data_and_the_look_rule() -> None:
    llm = V._mock(V._llm_out())  # noqa: SLF001
    plan = asyncio.run(vp.write(llm, V._prop(company_character=VIRTUAL), "ctx", V.PRODUCT))  # noqa: SLF001
    sent = _plan_calls(llm)[0]
    assert vp.CHARACTER_RULE.strip() in sent and vp.COMPANY_CHARACTER_KO in sent
    assert LOOK in sent.split(vp.CHARACTER_RULE.strip())[1]  # 외형은 지시문이 아니라 데이터 블록에
    assert plan.written_by == "llm" and plan.character_source == "company_character"
    assert "animated clip" not in plan.style


# ---------------------------------------------------------------- 없는 외형 낱말


def test_extra_look_words_are_found_only_when_not_in_the_character() -> None:
    p = V._prop(company_character=VIRTUAL)  # noqa: SLF001
    texts = [(1, "고양이 귀를 단 인물이 노래한다"), (2, "She waves, wearing glasses")]
    texts.append((3, "무대 위"))
    assert vp.look_problems(p, texts) == [(1, "고양이 귀"), (2, "glasses")]
    assert vp.look_problems(p, [(1, "a hatch opens")]) == []  # 낱말 경계 (hat ≠ hatch)
    with_hat = CompanyCharacter(kind="virtual_human", look_en=LOOK + ", a red hat", photo=True)
    assert vp.look_problems(V._prop(company_character=with_hat), [(1, "red hat")]) == []  # noqa: SLF001
    assert vp.look_problems(V._prop(), texts) == []  # noqa: SLF001 - 회사 캐릭터 없음


def test_extra_look_is_rewritten_once_then_kept() -> None:
    bad = V._llm_out(prompt="A woman with cat ears sings on a stage, slow push-in")  # noqa: SLF001
    good = V._llm_out(prompt="A woman with long black hair sings on a stage, slow push-in")  # noqa: SLF001
    llm = SeqLLM(plans=[bad, good])
    plan = asyncio.run(vp.write(llm, V._prop(company_character=VIRTUAL), "ctx", V.PRODUCT))  # noqa: SLF001
    sent = _plan_calls(llm)
    assert len(sent) == 2  # 처음 + 다시 쓰기 1회
    assert vp.FIX_HEAD.strip() in sent[1] and "'cat ears'" in sent[1]
    assert vp.FIX_HEAD.strip() not in sent[0]
    assert all("cat ears" not in s.prompt for s in plan.scenes)
    # 다시 써도 남으면 더 부르지 않는다
    llm2 = SeqLLM(plans=[bad, bad])
    asyncio.run(vp.write(llm2, V._prop(company_character=VIRTUAL), "ctx", V.PRODUCT))  # noqa: SLF001
    assert len(_plan_calls(llm2)) == 2


def test_markdown_shows_the_main_character_line() -> None:
    plan = vp.rule_plan(V._prop(company_character=VIRTUAL), V.PRODUCT)  # noqa: SLF001
    md = vp.markdown(plan)
    assert "**주인공**: 회사 캐릭터 (검은 긴 생머리·앞머리·흰 반팔) · 실사 그대로" in md
    assert "**주인공**" not in vp.markdown(vp.rule_plan(V._prop(), V.PRODUCT))  # noqa: SLF001
    assert vp.character_line(vp.rule_plan(V._prop(), V.PRODUCT)) == "회사 브랜드 기본 캐릭터"  # noqa: SLF001


# ---------------------------------------------------------------- 마케팅 시트 읽기


SHEET = {
    "kind": "virtual_human", "summary_ko": "검은 긴 생머리", "consistency_en": LOOK,
    "appearance_en": "x", "outfit_en": "y",
}  # fmt: skip


def test_sheet_to_character() -> None:
    c = ext.character_of(SHEET)
    assert c and c.photo and c.kind == "virtual_human" and c.look_en == LOOK
    drawn = ext.character_of(
        {"kind": "illustration", "appearance_en": "a cat", "outfit_en": "scarf"}
    )
    assert drawn and not drawn.photo and drawn.look_en == "a cat scarf"
    photo = {"kind": "real_photo", "consistency_en": "a boy", "stylized_from_photo": True}
    styled = ext.character_of(photo)
    assert styled and not styled.photo  # 그림체로 바꾼 사진은 그림체 그대로
    assert ext.character_of({"kind": "virtual_human"}) is None and ext.character_of(None) is None


class Marketing:
    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.asked: list[str] = []

    async def character(self, ctx, record_id):
        self.asked.append(record_id)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _receive(marketing: Any, record: str | None = RECORD) -> tuple[PlanningService, Any]:
    svc = PlanningService(
        gate=V._gate, relater=None, marketing=marketing, proposal_writer=V.Writer(None)  # noqa: SLF001
    )
    inp = V._insight().model_copy(update={"brand_character_record_id": record})  # noqa: SLF001
    res = asyncio.run(svc.receive_insight(requester="orchestrator", inp=inp))
    return svc, ReceiveInsightOutput.model_validate(res.output)


def test_orchestrator_relay_reads_the_chosen_character_and_keeps_it_on_the_proposal() -> None:
    m = Marketing(VIRTUAL)
    svc, out = _receive(m)
    assert m.asked == [RECORD]
    assert out.video_plan and out.video_plan.character_source == "company_character"
    p = next(x for x in svc.state.proposals if x.id == out.proposal_id)
    assert p.brand_character_record_id == RECORD and p.company_character == VIRTUAL


@pytest.mark.parametrize("answer", ["failed not_found", TimeoutError()])
def test_character_not_read_keeps_the_brand_character(answer: Any) -> None:
    svc, out = _receive(Marketing(answer))
    assert out.video_plan and out.video_plan.character_source == "brand"
    p = next(x for x in svc.state.proposals if x.id == out.proposal_id)
    assert p.brand_character_record_id == RECORD and p.company_character is None


def test_no_record_means_marketing_is_not_asked() -> None:
    m = Marketing(VIRTUAL)
    _, out = _receive(m, record=None)
    assert m.asked == [] and out.video_plan and out.video_plan.character_source == "brand"


def test_marketing_client_reads_the_record_of_the_request_company() -> None:
    seen: list[Any] = []

    async def call(target, req):
        seen.append((target, req.skill, req.input, req.meta.tenant_id))
        from ax_contracts.schemas import AgentResult, TaskStatus

        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output={"sheet": SHEET})

    client = ext.MarketingBrief(caller=call)
    with ext.company_scope.using("nubitour"):
        got = asyncio.run(client.character("ctx", RECORD))
    assert isinstance(got, CompanyCharacter) and got.photo
    assert seen == [("marketing", "creative_record_get", {"record_id": RECORD}, "nubitour")]
