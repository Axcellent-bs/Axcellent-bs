"""마케팅 확인 refs가 기획 제작 검사와 같은 공용 기준을 쓴다 (11/26, ax_agent_template.plan_rules).

낱말 목록과 판정은 공용 한 곳에만 있고, 마케팅 동작·출력은 그대로다."""

from __future__ import annotations

from ax_agent_template import plan_rules
from marketing_agent import plan_review as pr


def test_words_are_the_shared_ones() -> None:
    assert pr.OTHER_PERSON_WORDS is plan_rules.OTHER_PERSON_WORDS
    assert pr.CROWD_WORDS is plan_rules.CROWD_WORDS
    assert pr.WIDE_WORDS is plan_rules.WIDE_WORDS


def test_refs_follow_the_shared_rule() -> None:
    def plan(visual: str, camera: str = "") -> dict:
        return {"scenes": [{"n": 1, "start_s": 0, "end_s": 4, "visual": visual,
                            "camera_en": camera, "prompt": "A desk"}]}  # fmt: skip

    near = plan("회사 캐릭터가 친구와 마주 앉는다", "medium shot")
    assert [x["kind"] for x in pr.check(near, has_character=True)] == ["refs"]
    assert pr.check(near, has_character=False) == []
    assert pr.check(plan("회사 캐릭터 뒤 배경에 관객"), has_character=True) == []
    assert pr.check(plan("회사 캐릭터가 친구와 걷는다", "wide shot"), has_character=True) == []
    # 마케팅은 인원수 칸이 없어 낱말로만 본다 (예전 그대로)
    assert pr.check(plan("회사 캐릭터가 책을 읽는다"), has_character=True) == []
