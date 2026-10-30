"""업무 요청 라우터: 켠 서비스면 에이전트로, 안 켠 서비스면 사람 할 일함으로.

고객사는 서비스를 골라서 산다. 서비스 하나만 사도 다른 부서(사람)와 협업이 되고,
여러 개 사면 그 서비스 에이전트끼리 A2A로 협업한다.
`call_agent()`가 `route_target()`으로 받는 서비스를 정한 뒤 여기서 묻는다.

- 켠 서비스: `AX_ENABLED_SERVICES_FILE`이 가리키는 JSON 파일이 있으면 그 파일
  (`{"enabled": ["data", ...], "updated_at": "...", "updated_by": "..."}`, 재시작 없이 반영),
  없으면 환경변수 `AX_ENABLED_SERVICES` (쉼표 목록, 비어 있으면 전부 켬).
  파일이 깨졌거나 읽을 수 없으면 환경변수 값을 쓴다(경고 로그).
  `services.py`의 서비스 13개만 이 목록을 따른다. 그 밖의 대상
  (safety·memory·shop-api·dashboard-api 같은 공통 서버, 주소로 부른 대상)은 늘 켜져 있다.
- 조직도: `AX_ORG_CONFIG`가 가리키는 YAML 파일 (예시 `backend/config/org.example.yaml`).
  업무(skill 또는 서비스 id) → 맡은 부서·담당자. skill을 먼저 찾고 없으면 서비스 id로 찾는다.
  부서 이름은 이 파일에만 있고 코드에는 쓰지 않는다.
  파일이 없거나 업무가 없으면 `None`(지어내지 않음).
- 목록·조회: 받는 서비스 카드에서 `grade: read` + `handoff: false`인 skill은 사람에게 넘기지 않는다
  (`is_lookup`). `call_agent`는 `rejected` + `service_disabled`로 답한다.

```yaml
assignments:
  inquiry: {unit: 고객지원}
  rollback_deployment: {unit: 서비스운영, assignee: 당직 담당}
```
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ax_agent_template.services import BY_ID

ENABLED_ENV = "AX_ENABLED_SERVICES"
ENABLED_FILE_ENV = "AX_ENABLED_SERVICES_FILE"
ORG_ENV = "AX_ORG_CONFIG"
CARDS_ENV = "AX_SERVICE_CARDS_DIR"
# 서비스 카드 폴더 (`backend/services/<id>/agent-card.json`). 이 파일 기준 backend/ 아래
_DEFAULT_CARDS = Path(__file__).resolve().parents[4] / "services"

logger = logging.getLogger("ax.routing")


@dataclass(frozen=True)
class Assignment:
    """업무를 맡은 부서(`unit`)와 담당자(`assignee`, 조직도에 없으면 None)."""

    unit: str
    assignee: str | None = None


def _enabled_from_env() -> frozenset[str] | None:
    raw = os.getenv(ENABLED_ENV, "")
    names = frozenset(n.strip() for n in raw.split(",") if n.strip())
    return names or None


# 켠 서비스 파일: 바뀐 시각·크기가 같으면 다시 읽지 않는다. 실패도 같은 상태면 경고를 한 번만 남긴다
_enabled_cache: tuple[str, int, int, frozenset[str] | None] | None = None


def _parse_enabled(raw: Any, path: str) -> frozenset[str]:
    names = raw.get("enabled") if isinstance(raw, dict) else None
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ValueError(f"{path}: enabled(서비스 id 문자열 목록)가 필요하다")
    return frozenset(n.strip() for n in names if n.strip())


def _enabled_from_file(path: str) -> frozenset[str] | None:
    """파일의 켠 서비스. 읽을 수 없거나 형식이 틀리면 환경변수 값(경고 로그)이다.

    전부 켬으로 바꾸지 않는다: 환경변수도 비어 있을 때만 전부 켬(지금과 같은 뜻)이다.
    """
    global _enabled_cache
    try:
        stat = os.stat(path)
    except OSError as exc:
        key = (path, -1, -1)
        if _enabled_cache is None or _enabled_cache[:3] != key:
            logger.warning(
                "켠 서비스 파일을 읽을 수 없어 %s 값을 쓴다: %s (%s)", ENABLED_ENV, path, exc
            )
            _enabled_cache = (*key, None)
        return _enabled_from_env()
    key = (path, stat.st_mtime_ns, stat.st_size)
    if _enabled_cache is not None and _enabled_cache[:3] == key:
        cached = _enabled_cache[3]
        return _enabled_from_env() if cached is None else cached
    try:
        names = _parse_enabled(json.loads(Path(path).read_text(encoding="utf-8")), path)
    except (OSError, ValueError) as exc:  # json.JSONDecodeError는 ValueError다
        logger.warning("켠 서비스 파일이 깨져 %s 값을 쓴다: %s (%s)", ENABLED_ENV, path, exc)
        _enabled_cache = (*key, None)
        return _enabled_from_env()
    _enabled_cache = (*key, names)
    return names


def enabled_services() -> frozenset[str] | None:
    """켠 서비스 id 목록. `None`이면 전부 켬.

    `AX_ENABLED_SERVICES_FILE`이 있으면 그 파일(`{"enabled": [...], "updated_at", "updated_by"}`)을
    부를 때마다 바뀐 시각으로 확인해 읽는다(재시작 없이 반영). 파일의 빈 목록은 "모두 끔"이다.
    파일이 없거나 깨졌으면 환경변수 `AX_ENABLED_SERVICES`(쉼표 목록, 비어 있으면 전부 켬)를 쓴다.
    """
    path = os.getenv(ENABLED_FILE_ENV, "").strip()
    if path:
        return _enabled_from_file(path)
    return _enabled_from_env()


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


# 서비스 카드의 skill 표: 서비스 id → {skill id → skill 정보}. 파일 내용이 바뀌면 다시 읽는다
_cards: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}


def _card_skills(service_id: str) -> dict[str, dict[str, Any]] | None:
    """받는 서비스의 Agent Card에서 skill 표를 읽는다. 카드를 못 읽으면 None.

    꺼진 서비스는 떠 있지 않을 수 있어서 HTTP가 아니라 카드 파일(`AX_SERVICE_CARDS_DIR`,
    기본 `backend/services`)을 읽는다.
    """
    base = Path(os.getenv(CARDS_ENV) or _DEFAULT_CARDS)
    path = base / service_id / "agent-card.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    cached = _cards.get(service_id)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        card = json.loads(path.read_text(encoding="utf-8"))
        skills = {s["id"]: s for s in card.get("skills", []) if isinstance(s, dict) and "id" in s}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        logger.error("서비스 카드를 읽지 못했다: %s (%s)", path, error)
        return None
    _cards[service_id] = (mtime, skills)
    return skills


def is_lookup(service_id: str, skill: str) -> bool:
    """꺼진 서비스로 가도 사람에게 넘기지 않을 목록·조회 skill인지.

    카드에서 `grade`가 `read`이고 `handoff`가 `false`일 때만 True. 카드를 못 읽거나 skill이 없으면
    False(지금처럼 넘긴다). `read`여도 공지 초안·영향 분석처럼 사람이 대신할 수 있는 일은 넘긴다.
    """
    skills = _card_skills(service_id)
    entry = (skills or {}).get(skill)
    return bool(entry) and entry.get("grade") == "read" and entry.get("handoff") is False


def reset_cache() -> None:
    """테스트 초기화용."""
    global _cache
    _cache = None
    _cards.clear()
