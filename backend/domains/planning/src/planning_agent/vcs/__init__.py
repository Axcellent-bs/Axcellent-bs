"""형상관리 연결 계층 (FR-AG-011, NFR-EXT-003).

연결(레포)을 여러 개 둘 수 있다. 도메인은 `get_adapter(연결 id)`·`get_vcs()`가 준 부품만 부른다.

- 화면(개발 도구 연결)에서 넣은 연결은 `upsert_connection()`으로 받는다. id는 `conn-1`, `conn-2` …
- 환경변수 `PLANNING_VCS_*`는 종류가 `mock`이 아니면 id `env` 연결 하나가 된다 (화면에서 못 바꾼다).

| 변수 | 내용 |
| --- | --- |
| `PLANNING_VCS_KIND` | `mock`(기본) · `github` |
| `PLANNING_VCS_REPO` | 대상 레포 `owner/name` |
| `PLANNING_VCS_TOKEN` | 접근 토큰 (로그에 남기지 않는다) |
| `PLANNING_VCS_BASE_URL` | API 주소 (기본 공개 주소, 사내 설치형이면 바꾼다) |
| `PLANNING_VCS_BASE_BRANCH` | 병합 대상 브랜치 (기본 `dev`) |
| `PLANNING_VCS_DESCRIPTION` | 레포 설명 (레포 고르기 근거) |
| `PLANNING_VCS_PATHS` | 담당 경로 (쉼표로 나눈다, 레포 고르기 근거) |

- 연결이 하나도 없으면 목 부품 하나만 쓴다 (도메인은 실제 PR을 올리지 않는다).
- 부품은 연결마다 한 번 만들어 둔다. 설정이 바뀌면 다시 만든다. 테스트는 `set_vcs()`로 바꿔 끼운다.
- 토큰은 이 프로세스 메모리에만 두고 화면·로그로 돌려주지 않는다 (넣었는지와 끝 4자리만).
- `get_vcs()`·`configure()`·`describe()`는 첫 연결을 보는 예전 화면용 길이다.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

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
    "Connection",
    "MockVcsAdapter",
    "OpenedChange",
    "TraceMeta",
    "VcsAdapter",
    "VcsError",
    "configure",
    "configured_kind",
    "connections",
    "delete_connection",
    "describe",
    "find_connection",
    "get_adapter",
    "get_vcs",
    "is_overridden",
    "list_connections",
    "parse_trace",
    "routing_connections",
    "set_vcs",
    "upsert_connection",
]

KINDS = ("mock", "github")
ENV_ID = "env"
OVERRIDE_ID = "default"
_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


@dataclass(frozen=True)
class Connection:
    """연결 하나 (레포 하나). 토큰은 repr·화면에 넣지 않는다."""

    id: str
    kind: str
    repo: str = ""
    base_url: str = ""
    base_branch: str = "dev"
    description: str = ""
    paths: tuple[str, ...] = ()
    # 이 레포가 회사 코드에서 놓인 자리 (예: 쇼핑몰 화면만 떼어 온 레포 → "shop-web/").
    # 안전 관문·역추적에는 이 앞머리를 붙인 경로로 보내고, 커밋은 레포 안 경로 그대로 한다
    mount: str = ""
    source: str = "screen"  # screen · env (바꿔 끼운 부품이면 override)
    token: str = field(default="", repr=False)

    def public(self) -> dict:
        """화면에 보여 줄 모양. 토큰 값 대신 넣었는지와 끝 4자리만 준다."""
        return {
            "id": self.id,
            "kind": self.kind,
            "repo": self.repo,
            "base_url": self.base_url,
            "base_branch": self.base_branch,
            "description": self.description,
            "paths": list(self.paths),
            "mount": self.mount,
            "token_set": bool(self.token),
            "token_hint": _hint(self.token),
            "source": self.source,
        }


def _hint(token: str) -> str:
    # 짧은 토큰은 끝자리도 보여 주지 않는다
    return f"••••{token[-4:]}" if len(token) >= 8 else ("••••" if token else "")


_cached: VcsAdapter | None = None  # 연결이 없을 때 쓰는 목 부품
_override: VcsAdapter | None = None
# 화면에서 넣은 연결 (id → 연결, 넣은 순서). None이면 환경변수만 쓴다
_screen: dict[str, Connection] | None = None
# 연결별 부품. 설정이 같을 때만 다시 쓴다
_adapters: dict[str, tuple[Connection, VcsAdapter]] = {}


def clean_paths(paths: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    """담당 경로를 다듬는다: 앞뒤 공백·앞 `/`·`./`를 떼고 빈 값·중복을 뺀다."""
    out: list[str] = []
    for raw in paths or ():
        p = str(raw).strip()
        while p.startswith(("./", "/")):
            p = p[2:] if p.startswith("./") else p[1:]
        if p and p not in out:
            out.append(p)
    return tuple(out)


def clean_mount(raw: str | None) -> str:
    """앞머리 경로를 `shop-web/`처럼 끝이 `/`인 상대 경로로 다듬는다. 비우면 레포 루트 그대로."""
    p = (raw or "").strip().replace("\\", "/")
    while p.startswith(("./", "/")):
        p = p[2:] if p.startswith("./") else p[1:]
    if not p:
        return ""
    if ".." in p.split("/"):
        raise VcsError("config", "앞머리 경로에 ..를 쓸 수 없다")
    return p if p.endswith("/") else p + "/"


def logical_paths(connection_id: str | None, paths: list[str] | tuple[str, ...]) -> list[str]:
    """레포 안 경로에 그 연결의 앞머리를 붙인다 (안전 관문·역추적용). 앞머리가 없으면 그대로."""
    conn = find_connection(connection_id) if connection_id else None
    mount = conn.mount if conn else ""
    return [p if not mount or p.startswith(mount) else mount + p for p in paths]


def _env_connection() -> Connection | None:
    kind = (os.getenv("PLANNING_VCS_KIND") or "").strip().lower()
    if not kind or kind == "mock":
        return None
    return Connection(
        id=ENV_ID,
        kind=kind,
        repo=os.getenv("PLANNING_VCS_REPO") or "",
        base_url=os.getenv("PLANNING_VCS_BASE_URL") or "",
        base_branch=os.getenv("PLANNING_VCS_BASE_BRANCH") or "dev",
        description=os.getenv("PLANNING_VCS_DESCRIPTION") or "",
        paths=clean_paths((os.getenv("PLANNING_VCS_PATHS") or "").split(",")),
        mount=clean_mount(os.getenv("PLANNING_VCS_MOUNT")),
        source="env",
        token=os.getenv("PLANNING_VCS_TOKEN") or "",
    )


def connections() -> list[Connection]:
    """설정된 연결 (화면 연결을 넣은 순서대로, 그다음 환경변수 연결)."""
    rows = list((_screen or {}).values())
    env = _env_connection()
    if env is not None:
        rows.append(env)
    return rows


def list_connections() -> list[dict]:
    return [c.public() for c in connections()]


def routing_connections() -> list[Connection]:
    """PR을 보낼 수 있는 연결. 부품을 바꿔 끼웠고 연결이 없으면 그 부품 하나를 연결로 본다."""
    rows = connections()
    if not rows and _override is not None:
        repo = str(getattr(_override, "repo", "") or "")
        rows = [Connection(id=OVERRIDE_ID, kind="override", repo=repo, source="override")]
    return rows


def find_connection(connection_id: str) -> Connection | None:
    return next((c for c in routing_connections() if c.id == connection_id), None)


def configured_kind() -> str:
    rows = connections()
    return rows[0].kind.strip().lower() if rows else "mock"


def _build(conn: Connection) -> VcsAdapter:
    kind = conn.kind.strip().lower()
    if kind == "mock":
        return MockVcsAdapter(base_branch=conn.base_branch or "dev")
    if kind == "github":
        from planning_agent.vcs.github import DEFAULT_BASE_URL, GithubVcsAdapter

        return GithubVcsAdapter(
            repo=conn.repo,
            token=conn.token,
            base_url=conn.base_url or DEFAULT_BASE_URL,
            base_branch=conn.base_branch or "dev",
        )
    raise VcsError("config", f"모르는 PLANNING_VCS_KIND: {kind} (가능: {', '.join(KINDS)})")


def get_adapter(connection_id: str) -> VcsAdapter:
    """연결 하나의 부품. 바꿔 끼운 부품이 있으면 어느 연결이든 그것을 준다.

    모르는 연결이면 `VcsError`(not_found), 설정이 틀리면 `VcsError`(config)."""
    if _override is not None:
        return _override
    conn = next((c for c in connections() if c.id == connection_id), None)
    if conn is None:
        raise VcsError("not_found", f"모르는 연결: {connection_id}")
    hit = _adapters.get(conn.id)
    if hit is not None and hit[0] == conn:
        return hit[1]
    adapter = _build(conn)
    _adapters[conn.id] = (conn, adapter)
    return adapter


def get_vcs() -> VcsAdapter:
    """첫 연결의 부품. 연결이 없으면 목 부품. 바꿔 끼운 부품이 있으면 그것을 준다."""
    global _cached
    if _override is not None:
        return _override
    rows = connections()
    if rows:
        return get_adapter(rows[0].id)
    if _cached is None:
        _cached = MockVcsAdapter(base_branch=os.getenv("PLANNING_VCS_BASE_BRANCH") or "dev")
    return _cached


def set_vcs(adapter: VcsAdapter | None) -> None:
    """부품을 바꿔 끼운다 (테스트용). None이면 바꿔 끼운 것을 빼고 설정을 다시 읽는다."""
    global _override, _cached
    _override = adapter
    _cached = None
    _adapters.clear()


def is_overridden() -> bool:
    return _override is not None


def _next_id(taken: dict[str, Connection]) -> str:
    nums = [int(m.group(1)) for k in taken if (m := re.fullmatch(r"conn-(\d+)", k))]
    return f"conn-{max(nums, default=0) + 1}"


def upsert_connection(
    *,
    kind: str,
    repo: str = "",
    base_url: str = "",
    base_branch: str = "",
    token: str | None = None,
    description: str = "",
    paths: list[str] | tuple[str, ...] | None = None,
    id: str | None = None,  # noqa: A002 - 화면 계약의 필드 이름
    mount: str | None = None,
) -> Connection:
    """화면 연결을 넣거나 바꾼다. 토큰을 비워 보내면 그 연결의 이전 토큰을 그대로 쓴다.

    부품을 바로 만들어 보고, 형식이 틀리면 `VcsError`(config)를 던지고 아무것도 바꾸지 않는다."""
    global _screen
    kind = (kind or "").strip().lower()
    if kind not in KINDS:
        raise VcsError("config", f"모르는 형상관리 종류: {kind} (가능: {', '.join(KINDS)})")
    rows = dict(_screen or {})
    cid = (id or "").strip()
    if cid == ENV_ID:
        raise VcsError("config", "환경변수 연결은 화면에서 바꾸지 않는다")
    if cid and not _ID.match(cid):
        raise VcsError("config", "연결 id는 영문·숫자·-·_ 40자까지다")
    cid = cid or _next_id(rows)
    before = rows.get(cid)
    new_token = (token or "").strip()
    # 토큰은 영문·숫자·기호만 된다 (한글 입력 상태로 치거나 다른 글자가 같이 붙여지면 요청 머리글을 못 만든다)
    if new_token and (not new_token.isascii() or any(ch.isspace() for ch in new_token)):
        raise VcsError("config", "토큰에 쓸 수 없는 글자(한글·공백 등)가 들어 있어요. 토큰을 다시 복사해 넣어 주세요")
    conn = Connection(
        id=cid,
        kind=kind,
        repo=repo.strip(),
        base_url=base_url.strip(),
        base_branch=base_branch.strip() or "dev",
        description=description.strip(),
        paths=clean_paths(paths),
        mount=clean_mount(mount) if mount is not None else (before.mount if before else ""),
        source="screen",
        token=new_token or (before.token if before else ""),
    )
    adapter = _build(conn)  # 틀리면 여기서 VcsError, 상태는 그대로
    rows[cid] = conn
    _screen = rows
    _adapters[cid] = (conn, adapter)
    return conn


def delete_connection(connection_id: str) -> bool:
    """화면 연결을 지운다. 없으면 False. 환경변수 연결은 `VcsError`(config)."""
    global _screen
    if connection_id == ENV_ID and _env_connection() is not None:
        raise VcsError("config", "환경변수 연결은 화면에서 지우지 않는다")
    rows = dict(_screen or {})
    if connection_id not in rows:
        return False
    del rows[connection_id]
    _screen = rows or None
    _adapters.pop(connection_id, None)
    return True


# ---------------------------------------------------------------- 예전 화면용 (첫 연결)


def configure(
    *,
    kind: str,
    repo: str = "",
    base_url: str = "",
    base_branch: str = "",
    token: str | None = None,
) -> None:
    """첫 화면 연결을 바꾼다 (없으면 새로 만든다). 설명·담당 경로는 그대로 둔다."""
    first = next(iter((_screen or {}).values()), None)
    upsert_connection(
        id=first.id if first else None,
        kind=kind,
        repo=repo,
        base_url=base_url,
        base_branch=base_branch,
        token=token,
        description=first.description if first else "",
        paths=first.paths if first else (),
    )


def describe() -> dict:
    """첫 연결을 예전 모양으로 보여 준다. 토큰 값은 넣지 않는다."""
    rows = connections()
    first = rows[0] if rows else None
    if first is None:
        return {
            "kind": "mock",
            "repo": "",
            "base_url": "",
            "base_branch": os.getenv("PLANNING_VCS_BASE_BRANCH") or "dev",
            "token_set": False,
            "token_hint": "",
            "source": "default",
            "kinds": list(KINDS),
            "connections": 0,
        }
    return {
        "kind": first.kind,
        "repo": first.repo,
        "base_url": first.base_url,
        "base_branch": first.base_branch,
        "token_set": bool(first.token),
        "token_hint": _hint(first.token),
        "source": first.source,
        "kinds": list(KINDS),
        "connections": len(rows),
    }
