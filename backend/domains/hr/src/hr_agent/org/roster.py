"""조직 명부: 직원마다 소속 팀을 붙인 가상 명부(예시).

지금은 발령 기록이 없어서 `emp_01`…`emp_10`을 조직도(`org.yaml`)의 부서에 차례로 나눈다.
부서 이름은 조직도에만 두고 코드에는 쓰지 않는다. 실제 명부가 아니므로 화면과 응답에
"가상 명부(예시)"라고 밝힌다. 발령 기록이 생기면 이 파일의 `example_roster` 자리에
기록으로 센 명부가 들어온다.
"""

from __future__ import annotations

from dataclasses import dataclass

from ax_agent_template.routing import load_org

# 시연용 가상 직원 수. 인수인계 명부(`roster/seed.py`)의 직원 ID와 같은 값이다.
EXAMPLE_MEMBER_COUNT = 10
# 전체 직원 수(가정). "전 직원 등록 완료"는 확정된 명부가 이 인원일 때만 표시할 수 있다
EXPECTED_MEMBER_COUNT = 10


@dataclass(frozen=True)
class OrgMember:
    member_id: str
    team: str


def org_teams() -> list[str]:
    """조직도에 적힌 부서(팀) 이름. 적힌 순서를 지키고 겹치는 이름은 한 번만 쓴다."""
    return list(dict.fromkeys(a.unit for a in load_org().values()))


def example_roster(teams: list[str]) -> list[OrgMember]:
    """가상 직원을 팀에 차례로 나눈다. 팀이 없으면 빈 명부다(지어내지 않는다)."""
    if not teams:
        return []
    return [
        OrgMember(f"emp_{n:02d}", teams[(n - 1) % len(teams)])
        for n in range(1, EXAMPLE_MEMBER_COUNT + 1)
    ]
