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
시스템 관리자가 화면(개발 도구 연결)에서 넣은 값은 `configure()`로 받아 환경변수보다 앞선다.
토큰은 이 프로세스 메모리에만 두고 화면·로그로 돌려주지 않는다(`describe()`는 넣었는지만 알려 준다).
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
    "configure",
    "configured_kind",
    "describe",
    "get_vcs",
    "is_overridden",
    "parse_trace",
    "set_vcs",
]

KINDS = ("mock", "github")

_cached: VcsAdapter | None = None
_override: VcsAdapter | None = None
# 화면에서 넣은 설정 (kind·repo·base_url·base_branch·token). None이면 환경변수만 쓴다
_screen: dict[str, str] | None = None


def _setting(name: str, default: str = "") -> str:
    if _screen is not None and _screen.get(name):
        return _screen[name]
    return os.getenv(f"PLANNING_VCS_{name.upper()}") or default


def configured_kind() -> str:
    return _setting("kind", "mock").strip().lower()


def _build() -> VcsAdapter:
    kind = configured_kind()
    base_branch = _setting("base_branch", "dev")
    if kind == "mock":
        return MockVcsAdapter(base_branch=base_branch)
    if kind == "github":
        from planning_agent.vcs.github import DEFAULT_BASE_URL, GithubVcsAdapter

        return GithubVcsAdapter(
            repo=_setting("repo"),
            token=_setting("token"),
            base_url=_setting("base_url", DEFAULT_BASE_URL),
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


def configure(
    *,
    kind: str,
    repo: str = "",
    base_url: str = "",
    base_branch: str = "",
    token: str | None = None,
) -> None:
    """화면에서 넣은 설정으로 바꾼다. 토큰을 비워 보내면 이전에 넣은 토큰을 그대로 쓴다.

    부품을 바로 만들어 보고, 형식이 틀리면 `VcsError`(config)를 던지고 이전 설정으로 되돌린다."""
    global _screen, _cached
    kind = (kind or "").strip().lower()
    if kind not in KINDS:
        raise VcsError("config", f"모르는 형상관리 종류: {kind} (가능: {', '.join(KINDS)})")
    before = _screen
    keep = (before or {}).get("token", "") if not token else token.strip()
    _screen = {
        "kind": kind,
        "repo": repo.strip(),
        "base_url": base_url.strip(),
        "base_branch": base_branch.strip(),
        "token": keep,
    }
    _cached = None
    try:
        get_vcs()
    except VcsError:
        _screen = before
        _cached = None
        raise


def describe() -> dict:
    """화면에 보여 줄 설정. 토큰 값은 넣지 않고 넣었는지와 끝 4자리만 준다."""
    token = _setting("token")
    return {
        "kind": configured_kind(),
        "repo": _setting("repo"),
        "base_url": _setting("base_url"),
        "base_branch": _setting("base_branch", "dev"),
        "token_set": bool(token),
        "token_hint": f"••••{token[-4:]}" if len(token) >= 8 else ("••••" if token else ""),
        "source": "screen" if _screen is not None else ("env" if os.getenv("PLANNING_VCS_KIND") else "default"),
        "kinds": list(KINDS),
    }
