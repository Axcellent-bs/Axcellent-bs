"""개발 서비스의 LLM 설정: `DEV_LLM_*` → 공통 `LLM_*` (기획 `PLANNING_LLM_*`와 따로).

- 개발(코딩 에이전트·개발 대화·저장소 고르기)은 기획과 다른 모델·키를 쓸 수 있다.
  예: 개발은 Claude Sonnet, 기획은 Gemini.
- 예전 설정 호환: `DEV_LLM_*`가 하나도 없고 `PLANNING_LLM_*`만 있으면 한동안 그걸 쓰고
  경고 로그를 한 번 남긴다 ("DEV_LLM_*로 옮기세요").
- 고객사 키 저장 파일도 `DEV_LLM_KEY_STORE` (예전 이름 `PLANNING_LLM_KEY_STORE`도 한동안 읽는다).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping

from ax_agent_template.llm import LLMSettings

log = logging.getLogger(__name__)

DEPT = "dev"  # LLM 클라이언트 부서 이름 (사용량 기록·`DEV_LLM_TIMEOUT_SEC`)
_FIELDS = ("MODEL", "BASE_URL", "API_KEY")
_warned: set[str] = set()


def _warn_once(what: str, old: str, new: str) -> None:
    if what in _warned:
        return
    _warned.add(what)
    log.warning("개발이 %s를 대신 씀 → %s로 옮기세요", old, new)


def settings(env: Mapping[str, str] | None = None) -> LLMSettings:
    """개발 LLM 설정. `DEV_LLM_*` → `LLM_*`, `DEV_LLM_*`가 없고 `PLANNING_LLM_*`만 있으면 그것."""
    source = os.environ if env is None else env
    has_dev = any(source.get(f"DEV_LLM_{f}") for f in _FIELDS)
    has_planning = any(source.get(f"PLANNING_LLM_{f}") for f in _FIELDS)
    if not has_dev and has_planning:
        _warn_once("llm", "PLANNING_LLM_*", "DEV_LLM_*")
        return LLMSettings.from_env("planning", env=source)
    return LLMSettings.from_env(DEPT, env=source)


def key_store_path() -> str:
    """고객사 키 저장 파일 경로: `DEV_LLM_KEY_STORE` (예전 `PLANNING_LLM_KEY_STORE`)."""
    path = os.getenv("DEV_LLM_KEY_STORE")
    if path:
        return path
    old = os.getenv("PLANNING_LLM_KEY_STORE")
    if old:
        _warn_once("key_store", "PLANNING_LLM_KEY_STORE", "DEV_LLM_KEY_STORE")
        return old
    return ""
