"""조직 명부: 직원마다 소속 팀을 붙인 기본 명부(예시).

발령 기록이 없을 때의 기본 명부는 팀원 6명(`emp_01`…`emp_06`)이 맡은 부서다. 누가 어느 부서인지는
조직도(`org.yaml`)의 `members`에 적고, 부서 이름은 조직도에만 두고 코드에는 쓰지 않는다.
`members`가 없으면 직원 6명을 조직도의 부서에 차례로 나눈다. 발령 기록으로 확정한 명부가 아니라서
화면과 응답에 "예시 직원 명단"이라고 밝힌다. 발령 기록이 생기면 그 기록으로 센 명부가 대신한다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ax_agent_template.routing import ORG_ENV, load_org

logger = logging.getLogger("hr.org.roster")

# 기본 명부의 직원 수: 우리 팀원 6명. 조직도에 `members`가 없을 때 차례로 나눌 인원이다.
EXAMPLE_MEMBER_COUNT = 6


def _expected_member_count() -> int:
    """전체 직원 수. 환경변수 `HR_EXPECTED_MEMBERS`로 정하고, 없거나 잘못되면 6(팀원 수)이다."""
    raw = os.environ.get("HR_EXPECTED_MEMBERS", "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else EXAMPLE_MEMBER_COUNT


# 전체 직원 수. "전 직원 등록 완료"는 확정된 명부가 이 인원일 때만 표시할 수 있다
EXPECTED_MEMBER_COUNT = _expected_member_count()


@dataclass(frozen=True)
class OrgMember:
    member_id: str
    team: str


def org_teams() -> list[str]:
    """조직도에 적힌 부서(팀) 이름. 적힌 순서를 지키고 겹치는 이름은 한 번만 쓴다."""
    return list(dict.fromkeys(a.unit for a in load_org().values()))


def _configured_members(teams: list[str]) -> list[OrgMember]:
    """조직도 `members`(직원 ID와 맡은 부서)를 읽는다. 없거나 틀린 줄은 건너뛴다."""
    path = os.environ.get(ORG_ENV, "").strip()
    if not path:
        return []
    try:
        raw: Any = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return []
    entries = raw.get("members") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        return []
    seen: set[str] = set()
    members: list[OrgMember] = []
    for entry in entries:
        member_id = entry.get("id") if isinstance(entry, dict) else None
        unit = entry.get("unit") if isinstance(entry, dict) else None
        if not isinstance(member_id, str) or not isinstance(unit, str):
            continue
        member_id, unit = member_id.strip(), unit.strip()
        if not member_id or member_id in seen or unit not in teams:
            logger.warning("조직도 members에서 건너뜀: %r", entry)
            continue
        seen.add(member_id)
        members.append(OrgMember(member_id, unit))
    return members


def example_roster(teams: list[str]) -> list[OrgMember]:
    """기본 명부. 조직도 `members`가 있으면 그대로, 없으면 직원을 팀에 차례로 나눈다.

    팀이 없으면 빈 명부다(지어내지 않는다).
    """
    if not teams:
        return []
    configured = _configured_members(teams)
    if configured:
        return configured
    return [
        OrgMember(f"emp_{n:02d}", teams[(n - 1) % len(teams)])
        for n in range(1, EXAMPLE_MEMBER_COUNT + 1)
    ]
