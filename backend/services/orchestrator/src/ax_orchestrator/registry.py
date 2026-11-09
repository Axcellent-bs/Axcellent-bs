"""Agent Card 수집·skill 레지스트리 (ORC-04).

시작 시 모든 부서 카드를 읽어 `skill → 부서` 표를 만든다. 수집에 실패한 부서는 `unavailable`로
표시하고, 그 부서의 skill은 "확인할 수 없음"으로 처리한다(이벤트는 멈추지 않는다).
같은 skill을 여러 부서가 내면 부서 이름 순으로 첫 부서를 쓰고 충돌을 기록한다.

꺼진 서비스(`AX_ENABLED_SERVICES`·켠 서비스 파일에 없는 서비스)는 서버가 떠 있지 않아 카드를 받을 수
없다. 그 서비스의 skill은 카드 대신 서비스 대응표(`ax_agent_template.services`)로 부서를 정한다
(`find`). 호출은 그대로 `call_agent`로 가서 라우터가 조직도 부서의 받은 요청으로 넘기고, 조회
skill(`handoff: false`)이면 `service_disabled`로 돌려준다. 켜져 있는데 카드를 못 받은 서비스는
지금처럼 "확인할 수 없음"이다(장애를 사람 일로 바꾸지 않는다).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ax_agent_template import routing
from ax_agent_template.services import SERVICES
from ax_contracts.schemas.agent_card import AgentCard

SELF = "orchestrator"
DISABLED_REASON = "꺼진 서비스: 카드 없이 서비스 대응표로 부서를 정함 (사람 받은 요청으로 넘김)"
CardFetcher = Callable[[str], Awaitable[dict[str, Any] | None]]


@dataclass
class SkillEntry:
    dept: str
    skill: str
    grade: str | None  # 꺼진 서비스 항목은 카드가 없어 None
    verifier: str | None
    priority: str
    callable_by_orchestrator: bool
    # 꺼진 서비스라 카드 없이 서비스 대응표로 정한 항목 (호출하면 라우터가 사람에게 넘긴다)
    disabled_service: str | None = None


@dataclass
class Registry:
    skills: dict[str, SkillEntry] = field(default_factory=dict)
    cards: dict[str, AgentCard] = field(default_factory=dict)
    unavailable: dict[str, str] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)

    async def load(self, depts: list[str], fetch: CardFetcher) -> None:
        self.skills.clear()
        self.cards.clear()
        self.unavailable.clear()
        self.conflicts.clear()
        for dept in sorted(depts):
            if all_disabled(dept):  # 서버가 없다: 카드를 받으러 가지 않고 서비스 대응표로 넘긴다
                self.unavailable[dept] = DISABLED_REASON
                continue
            try:
                raw = await fetch(dept)
            except Exception as exc:  # 연결 오류 등 — 이벤트를 멈추지 않는다
                self.unavailable[dept] = f"수집 실패: {type(exc).__name__}"
                continue
            if raw is None:
                self.unavailable[dept] = "수집 실패: 응답 없음"
                continue
            try:
                card = AgentCard.model_validate(raw)
            except ValueError:
                self.unavailable[dept] = "카드 형식 위반"
                continue
            if not card.skills:
                self.unavailable[dept] = "카드에 skill 없음"
                continue
            self.cards[dept] = card
            for skill in card.skills:
                if skill.id in self.skills:
                    self.conflicts.append(f"{skill.id}: {self.skills[skill.id].dept} / {dept}")
                    continue
                self.skills[skill.id] = SkillEntry(
                    dept=dept,
                    skill=skill.id,
                    grade=skill.grade,
                    verifier=skill.verifier,
                    priority=skill.priority,
                    callable_by_orchestrator=SELF in skill.allowed_requesters,
                )

    def find(self, skill: str) -> SkillEntry | None:
        """카드에서 찾고, 없으면 꺼진 서비스의 skill인지 서비스 대응표에서 찾는다."""
        return self.skills.get(skill) or disabled_entry(skill)

    def alternatives(self, skill: str, exclude: str) -> list[SkillEntry]:
        """같은 skill 이름을 가진 다른 부서(이관용)."""
        return [
            SkillEntry(dept, s.id, s.grade, s.verifier, s.priority, SELF in s.allowed_requesters)
            for dept, card in sorted(self.cards.items())
            if dept != exclude
            for s in card.skills
            if s.id == skill
        ]

    def view(self) -> dict[str, Any]:
        return {
            "depts": [
                {
                    "name": name,
                    "owner": card.owner,
                    "skills": [
                        {
                            "id": s.id,
                            "grade": s.grade,
                            "priority": s.priority,
                            "orchestrator": SELF in s.allowed_requesters,
                        }
                        for s in card.skills
                    ],
                }
                for name, card in sorted(self.cards.items())
            ],
            "unavailable": self.unavailable,
            "conflicts": self.conflicts,
        }


def disabled_entry(skill: str) -> SkillEntry | None:
    """꺼진 서비스가 맡은 skill이면 서비스 대응표로 정한 항목. 켠 서비스·모르는 skill이면 None.

    등급은 카드가 없어 모른다(None). 사람이 처리하므로 안전 관문 등급을 지어내지 않는다.
    """
    for service in SERVICES:
        if skill in service.skills and not routing.is_enabled(service.id):
            return SkillEntry(
                dept=service.department,
                skill=skill,
                grade=None,
                verifier=None,
                priority="required",
                callable_by_orchestrator=True,
                disabled_service=service.id,
            )
    return None


def all_disabled(dept: str) -> bool:
    """그 부서의 서비스(services.py)가 모두 꺼져 있는지. 서비스가 없는 부서(총괄 등)는 False."""
    services = [s for s in SERVICES if s.department == dept and s.skills]
    return bool(services) and not any(routing.is_enabled(s.id) for s in services)
