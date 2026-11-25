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
    assert "의뢰자 요청" in (llm.data or {})["story"]  # 의뢰문은 데이터로만 간다
    board = rec["storyboard"]
    assert board["script_used"] is True and board["cta_ko"] == "회사 AI 협업, 엑설런트!"
    assert board["shots"][0]["line_ko"] == "엑설런트, 얼마나 좋아!!"
    assert board["shots"][0]["speaker_ko"] == "여자"
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
