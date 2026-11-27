"""제작안이 등장 캐릭터를 모두 쓴다 (11/27 C · 남녀 두 인물 + 그림 캐릭터).

cast 각 기록을 마케팅 A2A로 동시에 읽어 역할별 외형을 얻고, 제작안 LLM 데이터 칸 cast와 지시
(장면마다 역할 이름, 외형 그대로)를 넣는다. 못 읽은 캐릭터는 이유를 남기고 나머지로 진행한다.
가짜 LLM·가짜 마케팅만 쓴다."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from ax_contracts.schemas.marketing_brand import CastMember
from ax_contracts.schemas.planning import ReceiveInsightOutput
from ax_workflow.models import CompanyCharacter
from planning_agent import plan_checks as pc
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


V = _load("video_plan_helpers_cast", "test_video_plan.py")
LEAD, PEER, MASCOT = "CR-aaaaaaaaaaaa", "CR-bbbbbbbbbbbb", "CR-cccccccccccc"
WOMAN = CompanyCharacter(kind="virtual_human",
                         look_en="a woman with long black hair and round glasses",
                         summary_ko="검은 긴 생머리·원형 안경", photo=True)  # fmt: skip
MAN = CompanyCharacter(kind="virtual_human", look_en="a man with short hair and a grey coat",
                       summary_ko="짧은 머리·회색 코트", photo=True)  # fmt: skip
CAT = CompanyCharacter(kind="illustration", look_en="an orange cat mascot with a green scarf",
                       summary_ko="초록 목도리 주황 고양이", photo=False)  # fmt: skip
CAST = [CastMember(record_id=LEAD, role_ko="주인공"), CastMember(record_id=PEER, role_ko="동료"),
        CastMember(record_id=MASCOT, role_ko="마스코트")]  # fmt: skip


class Marketing:
    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.asked: list[str] = []

    async def character(self, ctx: str, record_id: str) -> Any:
        self.asked.append(record_id)
        got = self.answers[record_id]
        if isinstance(got, Exception):
            raise got
        return got


def _receive(answers: dict[str, Any], cast: list[CastMember], llm: Any = None
             ) -> tuple[PlanningService, Any]:  # fmt: skip
    m = Marketing(answers)
    svc = PlanningService(gate=V._gate, relater=None, marketing=m, proposal_writer=V.Writer(llm))  # noqa: SLF001
    lead = cast[0].record_id if cast else None
    inp = V._insight().model_copy(  # noqa: SLF001
        update={"brand_character_record_id": lead, "cast": cast})
    res = asyncio.run(svc.receive_insight(requester="orchestrator", inp=inp))
    return svc, ReceiveInsightOutput.model_validate(res.output)


def _plan_data(llm: Any) -> dict[str, Any]:
    sent = V.plan_calls(llm)[0]
    return json.loads(sent.split("<untrusted_data>")[1].split("</untrusted_data>")[0])


@pytest.mark.parametrize("n", [2, 3])
def test_every_cast_member_goes_to_the_plan_llm_with_its_role(n: int) -> None:
    llm = V._mock(V._llm_out())  # noqa: SLF001
    answers = {LEAD: WOMAN, PEER: MAN, MASCOT: CAT}
    svc, out = _receive(answers, CAST[:n], llm)
    data = _plan_data(llm)
    assert [c["role"] for c in data["cast"]] == ["주인공", "동료", "마스코트"][:n]
    assert data["cast"][1]["look_en"] == MAN.look_en
    assert vp.CAST_RULE.strip() in V.plan_calls(llm)[0]  # 장면마다 역할 이름·외형 그대로
    plan = out.video_plan
    assert plan is not None and [x.role_ko for x in plan.cast] == ["주인공", "동료", "마스코트"][:n]
    assert plan.cast[0].summary_ko == WOMAN.summary_ko and plan.cast[0].look == "photo"
    assert plan.character_source == "company_character"  # 주인공 줄은 지금처럼 cast[0]
    p = svc.state.proposals[0]
    assert set(p.cast_characters) == {m.record_id for m in CAST[:n]}
    md = vp.markdown(plan)
    want = ("**등장 캐릭터**: 주인공 · 검은 긴 생머리·원형 안경 · 실사 그대로 / "
            "동료 · 짧은 머리·회색 코트")
    assert want in md


def test_one_unreadable_member_keeps_a_reason_and_the_rest_go_on() -> None:
    llm = V._mock(V._llm_out())  # noqa: SLF001
    svc, out = _receive({LEAD: WOMAN, PEER: "failed not_found", MASCOT: CAT}, CAST, llm)
    data = _plan_data(llm)
    assert [c["role"] for c in data["cast"]] == ["주인공", "마스코트"]  # 읽은 캐릭터만
    plan = out.video_plan
    assert plan is not None
    assert [(x.role_ko, x.reason) for x in plan.cast] == [("주인공", ""), ("동료", "read_failed"),
                                                         ("마스코트", "")]  # fmt: skip
    assert vp.cast_line_ko(plan.cast[1]) == "동료 · 못 읽음"
    assert svc.state.proposals[0].cast_reasons == {PEER: "read_failed"}


def test_no_cast_or_one_member_is_todays_result() -> None:
    llm = V._mock(V._llm_out())  # noqa: SLF001
    _, out = _receive({LEAD: WOMAN}, CAST[:1], llm)
    assert "cast" not in _plan_data(llm) and vp.CAST_RULE.strip() not in V.plan_calls(llm)[0]
    assert out.video_plan is not None and out.video_plan.cast == []
    assert out.video_plan.character_source == "company_character"
    p = V._prop()  # noqa: SLF001
    assert vp.cast_lines(p) == [] and vp.cast_count(p) == 0


def test_people_check_counts_only_people_beyond_the_cast() -> None:
    two = [{"visual": "주인공과 동료가 책상 앞에서 이야기한다", "people": 2,
            "camera_en": "medium shot"}]  # fmt: skip
    assert pc.feasibility(two, has_character=True, cast_n=2) == []  # 동료는 cast 캐릭터
    three = [{"visual": "주인공과 동료 옆에 손님 한 명이 서 있다", "people": 3,
              "camera_en": "medium shot"}]  # fmt: skip
    got = pc.feasibility(three, has_character=True, cast_n=2)
    assert [x.get("code") for x in got] == [pc.CLOSE_PERSON]
    crowd = [{"visual": "주인공과 동료 뒤로 군중", "people": 5, "camera_en": "wide shot"}]
    assert pc.feasibility(crowd, has_character=True, cast_n=2) == []
    # 한 명(회사 캐릭터)일 때는 지금처럼 #1911 기준
    assert [x.get("code") for x in pc.feasibility(two, has_character=True)] == [pc.CLOSE_PERSON]


def test_look_words_of_any_cast_member_are_not_invented() -> None:
    p = V._prop(company_character=MAN, cast=CAST[:2], cast_characters={PEER: WOMAN})  # noqa: SLF001
    assert vp.look_problems(p, [(1, "동료가 원형 안경을 고쳐 쓴다 (glasses)")]) == []
