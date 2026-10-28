"""업무 요청 라우터: 켠 서비스면 에이전트로, 안 켠 서비스면 사람 할 일함으로.

고객사는 서비스를 골라서 산다. 서비스 하나만 사도 다른 부서(사람)와 협업이 되고,
여러 개 사면 그 서비스 에이전트끼리 A2A로 협업한다.
`call_agent()`가 `route_target()`으로 받는 서비스를 정한 뒤 여기서 묻는다.

- 켠 서비스: 환경변수 `AX_ENABLED_SERVICES` (쉼표 목록, 비어 있으면 전부 켬).
  `services.py`의 서비스 13개만 이 목록을 따른다. 그 밖의 대상
  (safety·memory·shop-api·dashboard-api 같은 공통 서버, 주소로 부른 대상)은 늘 켜져 있다.
- 조직도: `AX_ORG_CONFIG`가 가리키는 YAML 파일 (예시 `backend/config/org.example.yaml`).
  업무(skill 또는 서비스 id) → 맡은 부서·담당자. skill을 먼저 찾고 없으면 서비스 id로 찾는다.
  부서 이름은 이 파일에만 있고 코드에는 쓰지 않는다.
  파일이 없거나 업무가 없으면 `None`(지어내지 않음).

```yaml
assignments:
  inquiry: {unit: 고객지원}
  rollback_deployment: {unit: 서비스운영, assignee: 당직 담당}
```
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ax_agent_template.services import BY_ID

ENABLED_ENV = "AX_ENABLED_SERVICES"
ORG_ENV = "AX_ORG_CONFIG"

logger = logging.getLogger("ax.routing")


@dataclass(frozen=True)
class Assignment:
    """업무를 맡은 부서(`unit`)와 담당자(`assignee`, 조직도에 없으면 None)."""

    unit: str
    assignee: str | None = None


def enabled_services() -> frozenset[str] | None:
    """켠 서비스 id 목록. 환경변수가 비어 있으면 `None`(전부 켬)."""
    raw = os.getenv(ENABLED_ENV, "")
    names = frozenset(n.strip() for n in raw.split(",") if n.strip())
    return names or None


def is_enabled(target: str) -> bool:
    """받는 쪽이 켜져 있는지. 서비스 13개가 아닌 대상은 늘 켜져 있다."""
    if target not in BY_ID:
        return True
    enabled = enabled_services()
    return enabled is None or target in enabled


# 조직도 파일은 바뀐 시각이 같으면 다시 읽지 않는다
_cache: tuple[str, float, dict[str, Assignment]] | None = None


def _parse(raw: Any, path: str) -> dict[str, Assignment]:
    table = raw.get("assignments") if isinstance(raw, dict) else None
    if not isinstance(table, dict):
        raise ValueError(f"{path}: 맨 위에 assignments 표가 필요하다")
    result: dict[str, Assignment] = {}
    for work, entry in table.items():
        unit = entry.get("unit") if isinstance(entry, dict) else None
        if not isinstance(unit, str) or not unit.strip():
            raise ValueError(f"{path}: {work}에 unit(맡은 부서)이 없다")
        assignee = entry.get("assignee")
        if assignee is not None and not isinstance(assignee, str):
            raise ValueError(f"{path}: {work}의 assignee는 문자열이어야 한다")
        result[str(work)] = Assignment(unit.strip(), (assignee or "").strip() or None)
    return result


def load_org(path: str | None = None) -> dict[str, Assignment]:
    """조직도를 읽는다. 파일이 없거나 형식이 틀리면 빈 표(경고 로그)다.

    맡은 곳을 지어내지 않는다.
    """
    global _cache
    path = path or os.getenv(ORG_ENV, "")
    if not path:
        return {}
    try:
        mtime = Path(path).stat().st_mtime
    except OSError:
        logger.warning("조직도 파일이 없다: %s", path)
        return {}
    if _cache is not None and _cache[0] == path and _cache[1] == mtime:
        return _cache[2]
    try:
        table = _parse(yaml.safe_load(Path(path).read_text(encoding="utf-8")), path)
    except (OSError, ValueError, yaml.YAMLError) as error:
        logger.error("조직도 파일을 읽지 못했다: %s", error)
        return {}
    _cache = (path, mtime, table)
    return table


def assignment_for(service_id: str, skill: str) -> Assignment | None:
    """업무를 맡은 부서·담당자. skill → 서비스 id 순서로 찾고, 없으면 None."""
    org = load_org()
    return org.get(skill) or org.get(service_id)


def reset_cache() -> None:
    """테스트 초기화용."""
    global _cache
    _cache = None
