"""조직도(`AX_ORG_CONFIG`)의 영역 목록. 계획 비교(`check_plan`)가 영역 이름을 확인할 때 쓴다.

인사 AI의 명부를 읽지 않고(도메인끼리 import하지 않는다) 같은 조직도 파일에서 직접 읽는다.
목 모드(시험·데모)는 가상 영역 목록을 쓴다.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import yaml

ORG_ENV = "AX_ORG_CONFIG"
_AREA = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# 목 모드(시험·데모)에서 쓰는 가상 영역
MOCK_AREAS = ["payment", "search", "catalog", "support-desk", "infra-ops", "inventory"]

logger = logging.getLogger("archive.org_areas")


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
