"""대화방 홍보 영상 의뢰의 대본 (11/25, `request_script`).

영상 초안 준비 때 의뢰 기록에 대본을 쓰고 스토리보드 칸에 대사·말하는 사람을 붙인다.
가짜 LLM, 메모리 DB로만 돈다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest

from marketing_agent import creative_records as cr
from marketing_agent import creative_render as rr
from marketing_agent import creative_script as cs
from marketing_agent import request_creative as rc
from marketing_agent import request_script as rs
from marketing_agent.video_draft import VideoPlanIn

SHEET = {
    "summary_ko": "포니테일 여성",
    "consistency_en": "The exact same woman.",
    "kind": "virtual_human",
}
ASK = (
    "AXcellent 홍보 숏츠 영상 만들어 주세요. 목소리는 밝은 젊은 여자 목소리. "
    '첫 대사는 정확히 "엑설런트, 얼마나 좋아!!" 로 시작하고, 뒤는 AXcellent 소개로 이어 주세요.'
)


def _plan(n: int = 4) -> VideoPlanIn:
    return VideoPlanIn.model_validate(
        {
            "concept": "회사 일을 AI 부서가 나눠 맡는 영상",
            "script": "바람 속에서 외치고 AI 부서에 일을 맡긴다.",
            "duration_s": 15,
            "scenes": [
                {"prompt": f"scene {i} prompt text", "visual": f"화면 {i}", "caption": f"자막 {i}"}
                for i in range(1, n + 1)
            ],
        }
    )


class ScriptLLM:
    """대본 답만 한다. 1장면 대사를 다르게 써도 코드가 의뢰문 첫 대사로 고친다."""

    def __init__(self, n: int) -> None:
        self.n = n
        self.data: dict[str, Any] | None = None

    async def structured(self, prompt: str, schema: type, untrusted: dict | None = None, **_: Any):
        self.data = untrusted
        scenes = [
            {
                "visual_ko": f"장면 {i}",
                "line_ko": f"대사 {i}",
                "speaker_ko": "내레이션",
                "weight": 1,
            }
            for i in range(1, self.n + 1)
        ]
        return cs.ScriptRead(title_ko="엑설런트", scenes=scenes, cta_ko="회사 AI 협업, 엑설런트!")


@pytest.fixture
def store() -> Iterator[cr.CreativeRecords]:
    s = cr.CreativeRecords(":memory:")
    cr.set_store(s)
    yield s
    cr.set_store(None)


def _record(store: cr.CreativeRecords, plan: VideoPlanIn) -> str:
    rid = store.add_sheet(SHEET, None, None)
    board = rc.plan_storyboard(plan, SHEET)
    assert board is not None
    store.attach_storyboard(rid, plan.script, len(board["shots"]), board)
    return rid


def test_first_line_and_voice_are_read_from_the_request() -> None:
    assert rs.first_line(ASK) == "엑설런트, 얼마나 좋아!!"
    assert rs.first_line("첫 대사: '안녕하세요'") == "안녕하세요"
    assert rs.first_line("대사는 자유롭게") is None
    assert rs.voice_speaker(ASK) == "여자"
    assert rs.voice_speaker("남성 목소리로") == "남자"
    assert rs.voice_speaker("") == ""


def test_the_request_script_keeps_the_first_line_and_voice(store: cr.CreativeRecords) -> None:
    plan = _plan()
    rid = _record(store, plan)
    llm = ScriptLLM(4)
    assert asyncio.run(rs.attach(rid, plan, ASK, lambda: llm, "ctx_wr")) is True
    rec = store.get(rid) or {}
    script = rec["script"]
    assert script["source"] == "llm" and len(script["scenes"]) == 4
    first = script["scenes"][0]
    assert first["line_ko"] == "엑설런트, 얼마나 좋아!!" and first["speaker_ko"] == "여자"
    assert all(s["speaker_ko"] == "여자" for s in script["scenes"])  # 내레이션도 여자 목소리
    story = (llm.data or {})["story"]
    assert story.startswith("의뢰자 요청")  # 의뢰 연출을 먼저(기획 장면은 참고)
    assert rs.REAL_PERSON_NOTE in story  # AI 가상 인물은 실사라고 알려 준다
    board = rec["storyboard"]
    assert board["script_used"] is True and board["cta_ko"] == "회사 AI 협업, 엑설런트!"
    assert board["shots"][0]["line_ko"] == "엑설런트, 얼마나 좋아!!"
    assert board["shots"][0]["speaker_ko"] == "여자"
    assert [sh["scene_ko"] for sh in board["shots"]] == ["장면 1", "장면 2", "장면 3", "장면 4"]
    assert board[rr.PLAN_DURATION_KEY] == 15  # 기획 길이는 그대로 남는다
    # 영상 프롬프트에 대사가 들어간다
    assert "엑설런트, 얼마나 좋아!!" in rr._video_prompt(rec)  # noqa: SLF001


def test_without_llm_the_plan_scenes_make_the_script(store: cr.CreativeRecords) -> None:
    plan = _plan(3)
    rid = _record(store, plan)
    assert asyncio.run(rs.attach(rid, plan, ASK, None)) is True
    script = (store.get(rid) or {})["script"]
    assert [s["visual_ko"] for s in script["scenes"]] == ["화면 1", "화면 2", "화면 3"]
    assert script["scenes"][1]["line_ko"] == ""  # 의뢰문 문장을 대사로 옮기지 않는다
    assert script["scenes"][0]["line_ko"] == "엑설런트, 얼마나 좋아!!"


def test_a_record_that_already_has_a_script_is_left_alone(store: cr.CreativeRecords) -> None:
    plan = _plan()
    rid = _record(store, plan)
    store.attach_script(rid, {"title_ko": "사람이 쓴 대본"}, None)
    assert asyncio.run(rs.attach(rid, plan, ASK, None)) is False
    assert (store.get(rid) or {})["script"]["title_ko"] == "사람이 쓴 대본"


def test_mismatched_panel_count_keeps_the_board() -> None:
    plan = _plan(4)
    board = rc.plan_storyboard(plan, SHEET) or {}
    short = rs.plan_script(_plan(2))
    assert short is not None
    assert rs.board_with_script(board, short) == board


def test_a_role_speaker_gets_the_requested_voice() -> None:
    """11/25 "직장인"처럼 역할만 적히면 목소리가 빠졌다. "여자 직장인"으로 붙인다."""
    base = rs.plan_script(_plan(3))
    assert base is not None
    rows = [
        base.scenes[0].model_copy(update={"line_ko": "안녕", "speaker_ko": "직장인"}),
        base.scenes[1].model_copy(update={"line_ko": "네", "speaker_ko": "여자 직원"}),
        base.scenes[2],  # 대사 없음
    ]
    out = rs.apply_request(base.model_copy(update={"scenes": rows}), ASK)
    assert [s.speaker_ko for s in out.scenes] == ["여자 직장인", "여자 직원", ""]
    assert out.scenes[0].line_ko == "엑설런트, 얼마나 좋아!!"


# ---------------------------------------------------------------- 기획 의뢰 대사 (11/26)

QUOTE = "오늘 읽을 책은 캣북스에서! 지금 캣북스에서 만나요!"


def _quoted_plan(caption_last: bool = True) -> VideoPlanIn:
    plan = _plan().model_dump()
    if caption_last:
        plan["scenes"][-1]["caption"] = QUOTE
    plan["request_rules"] = [
        {"kind": "quote", "text_ko": QUOTE, "kept": True, "where": "장면 4 자막"},
        {"kind": "prop", "text_ko": "마이크", "kept": True, "where": "장면 2"},
    ]
    return VideoPlanIn.model_validate(plan)


def test_the_plan_quote_goes_into_the_script_word_for_word(store: cr.CreativeRecords) -> None:
    plan = _quoted_plan()
    assert rs.plan_quotes(plan) == [QUOTE]
    rid = _record(store, plan)
    assert asyncio.run(rs.attach(rid, plan, "홍보 영상", lambda: ScriptLLM(4), "ctx")) is True
    scenes = (store.get(rid) or {})["script"]["scenes"]
    assert scenes[3]["line_ko"] == QUOTE  # 작가가 "대사 4"로 썼어도 자막 장면에 그대로
    assert [s["line_ko"] for s in scenes[:3]] == ["대사 1", "대사 2", "대사 3"]


def test_without_a_caption_the_quote_is_the_last_line_and_kept_if_already_there() -> None:
    base = rs.plan_script(_plan())
    assert base is not None
    out = rs.keep_quotes(base, [QUOTE])
    assert out.scenes[-1].line_ko == QUOTE and rs.missing_quotes(out, [QUOTE]) == []
    said = base.model_copy(
        update={"scenes": [base.scenes[0].model_copy(update={"line_ko": f"자, {QUOTE}"}),
                           *base.scenes[1:]]}
    )  # fmt: skip
    assert rs.keep_quotes(said, [QUOTE]) == said  # 이미 있으면 그대로


def test_old_plans_without_rules_keep_the_script_as_today() -> None:
    assert rs.plan_quotes(_plan()) == []
