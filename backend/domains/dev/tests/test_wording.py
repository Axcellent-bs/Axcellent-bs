"""화면에 보이는 문장에 내부 이름(generate_pr·held)이 그대로 나가지 않는다."""

from dev_agent.wording import gate_text


def test_gate_text_uses_plain_words():
    assert gate_text("generate_pr", "held") == "안전 점검에서 PR 열기 보류"
    assert gate_text("merge_pr", "rejected") == "안전 점검에서 머지 거절"
    assert gate_text("generate_pr") == "안전 점검에서 PR 열기 막힘"


def test_gate_text_keeps_unknown_names():
    assert gate_text("new_action", "odd") == "안전 점검에서 new_action odd"
