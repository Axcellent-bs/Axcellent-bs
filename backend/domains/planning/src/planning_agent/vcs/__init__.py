"""형상관리 연결 계층 (FR-AG-011, NFR-EXT-003).

어떤 프로그램을 쓸지는 설정 `PLANNING_VCS_*`로만 고른다. 도메인은 `get_vcs()`가 준 부품만 부른다.

| 변수 | 내용 |
| --- | --- |
| `PLANNING_VCS_KIND` | `mock`(기본) · `github` |
| `PLANNING_VCS_REPO` | 대상 레포 `owner/name` |
| `PLANNING_VCS_TOKEN` | 접근 토큰 (로그에 남기지 않는다) |
| `PLANNING_VCS_BASE_URL` | API 주소 (기본 공개 주소, 사내 설치형이면 바꾼다) |
| `PLANNING_VCS_BASE_BRANCH` | 병합 대상 브랜치 (기본 `dev`) |

부품은 프로세스마다 한 번 만들어 둔다. 테스트는 `set_vcs()`로 바꿔 끼운다.
"""

from __future__ import annotations

import os

from planning_agent.vcs.base import (
    BRANCH_PREFIX,
    Capabilities,
    Change,
    ChangedFile,
    CheckRun,
    Checks,
    OpenedChange,
    TraceMeta,
    VcsAdapter,
    VcsError,
    parse_trace,
)
from planning_agent.vcs.mock import MockVcsAdapter

__all__ = [
    "BRANCH_PREFIX",
    "Capabilities",
    "Change",
    "ChangedFile",
    "CheckRun",
    "Checks",
    "MockVcsAdapter",
    "OpenedChange",
    "TraceMeta",
    "VcsAdapter",
    "VcsError",
    "configured_kind",
    "get_vcs",
    "is_overridden",
    "parse_trace",
    "set_vcs",
]

KINDS = ("mock", "github")

_cached: VcsAdapter | None = None
_override: VcsAdapter | None = None


def configured_kind() -> str:
    return (os.getenv("PLANNING_VCS_KIND") or "mock").strip().lower()


def _build() -> VcsAdapter:
    kind = configured_kind()
    base_branch = os.getenv("PLANNING_VCS_BASE_BRANCH") or "dev"
    if kind == "mock":
        return MockVcsAdapter(base_branch=base_branch)
    if kind == "github":
        from planning_agent.vcs.github import DEFAULT_BASE_URL, GithubVcsAdapter

        return GithubVcsAdapter(
            repo=os.getenv("PLANNING_VCS_REPO") or "",
            token=os.getenv("PLANNING_VCS_TOKEN") or "",
            base_url=os.getenv("PLANNING_VCS_BASE_URL") or DEFAULT_BASE_URL,
            base_branch=base_branch,
        )
    raise VcsError("config", f"모르는 PLANNING_VCS_KIND: {kind} (가능: {', '.join(KINDS)})")


def get_vcs() -> VcsAdapter:
    """설정에 맞는 부품. 바꿔 끼운 부품이 있으면 그것을 준다."""
    global _cached
    if _override is not None:
        return _override
    if _cached is None:
        _cached = _build()
    return _cached


def set_vcs(adapter: VcsAdapter | None) -> None:
    """부품을 바꿔 끼운다 (테스트용). None이면 바꿔 끼운 것을 빼고 설정을 다시 읽는다."""
    global _override, _cached
    _override = adapter
    _cached = None


def is_overridden() -> bool:
    return _override is not None
