"""서비스 대응표(services.py)의 skill 목록과 각 서비스가 실제로 공개하는 카드가 같은지 본다.

카드에 없는 skill은 템플릿 ACL이 막고 총괄도 담당 부서를 못 찾는다. 대응표만 고치고 서비스 카드를
빼먹으면 단위 테스트는 통과하는데 실제 모드에서만 skill이 건너뛰어진다.
"""

import json
from pathlib import Path

import pytest

from ax_agent_template.services import SERVICES

ROOT = Path(__file__).resolve().parents[3]


# skill이 아직 없는 서비스(예: hosts)는 카드도 없다
@pytest.mark.parametrize("service", [s for s in SERVICES if s.skills], ids=lambda s: s.id)
def test_service_card_lists_exactly_the_routed_skills(service) -> None:
    card = json.loads((ROOT / "services" / service.id / "agent-card.json").read_text("utf-8"))
    assert {s["id"] for s in card["skills"]} == set(service.skills)
