"""꺼진 인사 서비스를 부를 때 사람 할 일함으로 넘기지 않아야 하는 조회 skill.

라우터는 카드의 grade가 read이고 handoff가 false일 때만 넘기지 않는다. 시각 기록(grade A)은
카드 표시로 막을 수 없어 총괄이 꺼진 인사에는 보내지 않는다(orchestrator tests/test_event_times.py).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CARDS = [ROOT / "domains/hr/agent-card.json", ROOT / "services/decisions/agent-card.json"]
NO_HANDOFF = ("find_similar_cases",)


@pytest.mark.parametrize("path", CARDS, ids=lambda p: p.parent.name)
def test_the_similar_case_lookup_never_goes_to_the_human_inbox(path: Path) -> None:
    skills = {s["id"]: s for s in json.loads(path.read_text(encoding="utf-8"))["skills"]}
    for name in NO_HANDOFF:
        assert skills[name].get("handoff") is False, f"{path.name}: {name}"
