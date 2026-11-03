"""연결 대장: 연결마다 서버 종류·버전·토큰 권한·확인 시각 (FR-AG-012 연결 시 보안 조건).

- 연결을 넣거나 바꿀 때 한 번, 그 뒤로 하루마다(`PLANNING_VCS_CHECK_HOURS`, 기본 24) 다시 읽는다.
- 사내 설치형 서버는 보안 패치 기준 버전(`PLANNING_VCS_MIN_VERSIONS`)과 맞춰 본다.
  예: `ghes=3.14.0,gitlab=18.11.13`. 기준이 없으면 `기준 없음`으로 둔다 (버전은 기록한다).
- 클라우드(github.com 등)는 제공사가 패치하므로 버전 점검 대상이 아니다.
- 토큰 권한이 필요보다 넓으면 경고를 붙인다 (사람 토큰 범위·App 권한).
- 대장은 이 프로세스 메모리에만 둔다 (비밀 값 없음, 다시 켜면 처음부터 다시 읽는다).

점검 인터페이스 (연결 종류와 상관없음): 연결 종류마다 `register_source(종류, 목록 함수)`로
점검 대상(`Target`)을 내놓는다. 대상 하나는 `probe()`로 `ServerInfo`(제품·버전·권한·만료)를 준다.
- 형상관리(`vcs`)가 첫 대상이다 (GitHub 부품의 `server_info()`). GitLab도 같은 부품 방식으로 붙는다.
- 배포 도구(Jenkins `X-Jenkins` 머리글, Argo CD `/api/version` 등)는 그 연결 계층이 생기면 같은
  방식으로 `deploy` 종류를 등록한다. 대장·판정·화면·API는 그대로 쓴다.
- 패치 기준 출처는 `policy_source`에 남긴다: 지금은 `env`(`PLANNING_VCS_MIN_VERSIONS`), 취약점 소식
  자동 수집이 들어가면 `feed`.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
from collections.abc import Awaitable, Callable
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


@dataclass(frozen=True)
class Target:
    """점검 대상 하나 (연결 하나). 비밀 값은 넣지 않는다. `probe`가 없으면 `점검 전`으로 둔다."""

    id: str  # 연결 id (형상관리 conn-1 · env …)
    category: str  # vcs · deploy …
    name: str  # 화면에 보일 이름 (레포 owner/name, 배포 도구 주소 등)
    kind: str  # github · gitlab · jenkins · argocd · mock …
    base_url: str = ""
    app_auth: bool = False  # App 권한(이름:수준) 방식이면 True
    probe: Callable[[], Awaitable[ServerInfo]] | None = field(default=None, compare=False)
    no_probe: str = ""  # probe가 없는 이유


@dataclass
class Entry:
    """연결 하나의 대장 줄. 비밀 값은 넣지 않는다."""

    connection_id: str
    repo: str  # 대상 이름 (형상관리면 레포)
    kind: str
    category: str = "vcs"
    base_url: str = ""
    product: str = ""
    hosted: bool = False
    version: str = ""
    min_version: str = ""
    policy_source: str = ""  # 패치 기준 출처: env · feed (없으면 빈 값)
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
    entry.policy_source = "env" if entry.min_version else ""
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


def _vcs_targets() -> list[Target]:
    """형상관리 연결 → 점검 대상. 부품에 `server_info()`가 있으면 그것으로 읽는다."""
    out: list[Target] = []
    for conn in vcs.connections():

        async def probe(cid: str = conn.id) -> ServerInfo:
            reader = getattr(vcs.get_adapter(cid), "server_info", None)
            if reader is None:
                raise VcsError("unsupported", "이 형상관리 부품은 서버 정보를 못 읽음")
            return await reader()

        out.append(
            Target(
                id=conn.id,
                category="vcs",
                name=conn.repo,
                kind=conn.kind,
                base_url=conn.base_url,
                app_auth=conn.auth == "app",
                probe=probe,
            )
        )
    return out


# 연결 종류 → 점검 대상 목록 함수. 새 종류(배포 도구 등)는 register_source로 더한다
_sources: dict[str, Callable[[], list[Target]]] = {"vcs": _vcs_targets}


def register_source(category: str, lister: Callable[[], list[Target]]) -> None:
    """연결 종류 하나의 점검 대상 목록 함수를 등록한다 (같은 종류면 바꿔 낀다)."""
    _sources[category] = lister


def unregister_source(category: str) -> None:
    if category != "vcs":
        _sources.pop(category, None)


def targets() -> list[Target]:
    """등록된 모든 종류의 점검 대상 (종류 등록 순서, 그 안은 연결 순서)."""
    return [t for lister in _sources.values() for t in lister()]


def _blank(t: Target) -> Entry:
    return Entry(
        connection_id=t.id, repo=t.name, kind=t.kind, category=t.category, base_url=t.base_url
    )


async def check(connection_id: str, *, timeout: float = 20.0) -> Entry | None:
    """연결 하나를 다시 읽는다. 모르는 연결이면 None. 실패는 대장에 `error`로 남긴다."""
    t = next((x for x in targets() if x.id == connection_id), None)
    if t is None:
        _entries.pop(connection_id, None)
        return None
    entry = _blank(t)
    entry.checked_at = _clock().isoformat()
    try:
        if t.probe is None:
            entry.status = "unknown"
            entry.warnings = [t.no_probe or "이 연결 종류는 서버 정보를 못 읽음"]
        else:
            info = await asyncio.wait_for(t.probe(), timeout)
            judge(entry, info, min_versions(), app=t.app_auth)
    except VcsError as exc:
        if exc.code == "unsupported":
            entry.status, entry.warnings = "unknown", [exc.message]
        else:
            entry.status, entry.error_code, entry.error = "error", exc.code, exc.message
    except TimeoutError:
        entry.status, entry.error_code, entry.error = "error", "timeout", "서버 정보 읽기 시간 초과"
    _entries[t.id] = entry
    return entry


async def check_all() -> list[Entry]:
    """연결 전부를 다시 읽는다 (지운 연결은 대장에서 뺀다)."""
    ids = [t.id for t in targets()]
    for gone in set(_entries) - set(ids):
        _entries.pop(gone, None)
    got = await asyncio.gather(*(check(i) for i in ids))
    return [e for e in got if e is not None]


def entries() -> list[dict]:
    """대장 전체 (대상 순서). 아직 안 읽은 연결도 `unknown`으로 넣는다."""
    out: list[dict] = []
    for t in targets():
        hit = _entries.get(t.id)
        out.append(asdict(hit if hit is not None and hit.repo == t.name else _blank(t)))
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
