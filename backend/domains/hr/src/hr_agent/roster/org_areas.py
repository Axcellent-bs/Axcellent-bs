"""조직도의 영역 목록을 명부(`areas`)에 넣는다 (실제 모드).

`check_plan`은 명부에 없는 영역을 `UNKNOWN_AREA`로 거절한다. 목 모드는 `seed_roster`가
영역을 채우지만 실제 모드에는 영역을 만드는 곳이 없어서, 기획 AI가 보낸 초안이 늘
"확인 못 함"으로 멈췄다.
회사 조직도(`AX_ORG_CONFIG`, `config/tenants/<회사>/org.yaml`)의 `areas` 목록을 읽어 영역만 넣는다.

- 영역 이름만 넣는다. 담당자(`roster_entries`)·교대는 지어내지 않는다.
- 없는 영역만 더한다. 이미 있는 영역과 담당은 건드리지 않고, 지우지도 않는다.
- 조직도가 없거나 `areas`가 없거나 형식이 틀리면 아무것도 넣지 않는다(경고 로그).
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import yaml

from hr_agent.store import Store

ORG_ENV = "AX_ORG_CONFIG"
_AREA = re.compile(r"^[a-z0-9][a-z0-9-]*$")

logger = logging.getLogger("hr.roster.org_areas")


def load_org_areas(path: str | None = None) -> list[str]:
    """조직도 파일의 `areas` 목록. 읽을 수 없거나 형식이 틀리면 빈 목록이다."""
    path = path or os.getenv(ORG_ENV, "")
    if not path:
        return []
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        logger.warning("조직도 파일을 읽지 못했다: %s", error)
        return []
    names = raw.get("areas") if isinstance(raw, dict) else None
    if names is None:
        return []
    if not isinstance(names, list) or not all(isinstance(n, str) and _AREA.match(n) for n in names):
        logger.error("%s: areas는 영문 소문자·숫자·'-'로 된 이름의 목록이어야 한다", path)
        return []
    return list(dict.fromkeys(names))


def sync_areas(store: Store, areas: list[str]) -> list[str]:
    """명부에 없는 영역만 더한다. 더한 영역을 돌려준다."""
    known = {r["area"] for r in store.all("SELECT area FROM areas")}
    added = [a for a in areas if a not in known]
    if added:
        with store.transaction():
            for area in added:
                store.execute("INSERT INTO areas(area) VALUES (?)", (area,))
    return added
