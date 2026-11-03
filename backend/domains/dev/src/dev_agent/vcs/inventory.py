"""연결 대장: 연결마다 서버 종류·버전·토큰 권한·확인 시각 (FR-AG-012 연결 시 보안 조건).

- 연결을 넣거나 바꿀 때 한 번, 그 뒤로 하루마다(`PLANNING_VCS_CHECK_HOURS`, 기본 24) 다시 읽는다.
- 사내 설치형 서버는 보안 패치 기준 버전(`PLANNING_VCS_MIN_VERSIONS`)과 맞춰 본다.
  예: `ghes=3.14.0,gitlab=18.11.13`. 기준이 없으면 `기준 없음`으로 둔다 (버전은 기록한다).
- 클라우드(github.com 등)는 제공사가 패치하므로 버전 점검 대상이 아니다.
- 토큰 권한이 필요보다 넓으면 경고를 붙인다 (사람 토큰 범위·App 권한).
- 대장은 이 프로세스 메모리에만 둔다 (비밀 값 없음, 다시 켜면 처음부터 다시 읽는다).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Literal

from dev_agent import vcs
from dev_agent.vcs.base import ServerInfo, VcsError

Status = Literal["ok", "outdated", "no_policy", "hosted", "unknown", "error"]

# 사람 토큰(클래식) 범위 중 개발 AI에 필요 없는 넓은 권한
BROAD_SCOPES = ("admin:", "delete_repo", "delete:packages", "site_admin", "admin:enterprise")
# GitHub App 권한 중 개발 AI에 필요 없는 것 (쓰기면 경고)
NEEDED_APP = {"contents", "pull_requests", "issues", "checks", "statuses", "metadata"}

_VERSION = re.compile(r"\d+(?:\.\d+)*")


@dataclass
class Entry:
    """연결 하나의 대장 줄. 비밀 값은 넣지 않는다."""

    connection_id: str
    repo: str
    kind: str
    base_url: str = ""
    product: str = ""
    hosted: bool = False
    version: str = ""
    min_version: str = ""
    scopes: list[str] = field(default_factory=list)
    scope_note: str = ""
    token_expires: str = ""
    status: Status = "unknown"
    warnings: list[str] = field(default_factory=list)
    checked_at: str = ""
    error_code: str = ""
    error: str = ""


def _parse(v: str) -> tuple[int, ...] | None:
    m = _VERSION.search(v or "")
    return tuple(int(x) for x in m.group(0).split(".")) if m else None


def older(version: str, minimum: str) -> bool | None:
    """version이 minimum보다 낮으면 True. 둘 중 하나라도 못 읽으면 None."""
    a, b = _parse(version), _parse(minimum)
    if a is None or b is None:
        return None
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) < b + (0,) * (n - len(b))


def min_versions() -> dict[str, str]:
    """`PLANNING_VCS_MIN_VERSIONS=ghes=3.14.0,gitlab=18.11.13` → {제품: 기준 버전}."""
    out: dict[str, str] = {}
    for part in (os.getenv("PLANNING_VCS_MIN_VERSIONS") or "").split(","):
        key, _, val = part.partition("=")
        if key.strip() and val.strip():
            out[key.strip().lower()] = val.strip()
    return out


def scope_warnings(info: ServerInfo, *, app: bool) -> list[str]:
    """필요보다 넓은 권한. App은 `이름:수준`, 사람 토큰(클래식)은 범위 이름."""
    out: list[str] = []
    for s in info.scopes:
        if app:
            name, _, level = s.partition(":")
            if name not in NEEDED_APP and level in ("write", "admin"):
                out.append(f"필요 없는 App {level} 권한: {name}")
        elif any(s == b or s.startswith(b) for b in BROAD_SCOPES):
            out.append(f"넓은 토큰 범위: {s}")
    return out


def judge(entry: Entry, info: ServerInfo, policy: dict[str, str], *, app: bool = False) -> None:
    """서버 정보로 대장 줄의 상태·경고를 채운다."""
    entry.product = info.product
    entry.hosted = info.hosted
    entry.version = info.version
    entry.scopes = list(info.scopes)
    entry.scope_note = info.scope_note
    entry.token_expires = info.token_expires
    entry.warnings = scope_warnings(info, app=app)
    entry.min_version = policy.get(info.product, "")
    if info.hosted:
        entry.status = "hosted"
    elif not info.version:
        entry.status = "unknown"
        entry.warnings.append("서버 버전을 못 읽음")
    elif not entry.min_version:
        entry.status = "no_policy"
    else:
        low = older(info.version, entry.min_version)
        if low is None:
            entry.status = "unknown"
        elif low:
            entry.status = "outdated"
            entry.warnings.insert(
                0, f"보안 패치 기준({entry.min_version})보다 낮은 버전({info.version})"
            )
        else:
            entry.status = "ok"


_entries: dict[str, Entry] = {}
_task: asyncio.Task | None = None
_clock: Callable[[], datetime] = lambda: datetime.now(UTC)  # noqa: E731 - 테스트가 바꿔 끼운다


def _blank(conn: vcs.Connection) -> Entry:
    return Entry(connection_id=conn.id, repo=conn.repo, kind=conn.kind, base_url=conn.base_url)


async def check(connection_id: str, *, timeout: float = 20.0) -> Entry | None:
    """연결 하나를 다시 읽는다. 모르는 연결이면 None. 실패는 대장에 `error`로 남긴다."""
    conn = next((c for c in vcs.connections() if c.id == connection_id), None)
    if conn is None:
        _entries.pop(connection_id, None)
        return None
    entry = _blank(conn)
    entry.checked_at = _clock().isoformat()
    try:
        adapter = vcs.get_adapter(conn.id)
        reader = getattr(adapter, "server_info", None)
        if reader is None:
            entry.status = "unknown"
            entry.warnings = ["이 형상관리 부품은 서버 정보를 못 읽음"]
        else:
            info = await asyncio.wait_for(reader(), timeout)
            judge(entry, info, min_versions(), app=conn.auth == "app")
    except VcsError as exc:
        entry.status, entry.error_code, entry.error = "error", exc.code, exc.message
    except TimeoutError:
        entry.status, entry.error_code, entry.error = "error", "timeout", "서버 정보 읽기 시간 초과"
    _entries[conn.id] = entry
    return entry


async def check_all() -> list[Entry]:
    """연결 전부를 다시 읽는다 (지운 연결은 대장에서 뺀다)."""
    ids = [c.id for c in vcs.connections()]
    for gone in set(_entries) - set(ids):
        _entries.pop(gone, None)
    got = await asyncio.gather(*(check(i) for i in ids))
    return [e for e in got if e is not None]


def entries() -> list[dict]:
    """대장 전체 (연결 순서). 아직 안 읽은 연결도 `unknown`으로 넣는다."""
    out: list[dict] = []
    for conn in vcs.connections():
        hit = _entries.get(conn.id)
        out.append(asdict(hit if hit is not None and hit.repo == conn.repo else _blank(conn)))
    return out


def forget(connection_id: str) -> None:
    _entries.pop(connection_id, None)


def reset() -> None:
    _entries.clear()


def _hours() -> float:
    try:
        return max(1.0, float(os.getenv("PLANNING_VCS_CHECK_HOURS") or 24))
    except ValueError:
        return 24.0


def start_daily() -> None:
    """켤 때 한 번 읽고, 그 뒤 정해진 시간마다 다시 읽는다 (이미 돌고 있으면 그대로)."""
    global _task
    if _task is not None and not _task.done():
        return

    async def loop() -> None:
        while True:
            with contextlib.suppress(Exception):
                await check_all()
            await asyncio.sleep(_hours() * 3600)

    _task = asyncio.get_running_loop().create_task(loop())
