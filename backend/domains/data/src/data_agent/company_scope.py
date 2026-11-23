"""data 저장소의 회사 칸 도우미 (여러 회사 설계 1-5, 2026-11-23). 마케팅 1-6(#1557)과 같은 모양.

domains끼리 import하지 않으므로 마케팅 `company_scope.py`를 복사해 data에 맞게 더했다.

- 회사 칸: 회사 데이터 표마다 `tenant_id`(회사 ID) 칸. 옛 DB는 켤 때 `ALTER TABLE ... ADD COLUMN
  tenant_id TEXT NOT NULL DEFAULT 'catbooks'`로 칸을 붙여 기존 줄이 캣북스가 된다(멱등)
- 기본 키에 회사가 들어가야 하는 표는 ALTER로 안 되므로 다시 만들어 옮긴다
  (`rebuild_with_tenant_key`):
  새 이름으로 만들고 → 기존 줄을 캣북스로 복사 → 옛 표를 지우고 → 새 표 이름을 바꾼다. 한 트랜잭션.
  키에 이미 회사가 있으면 건너뛴다(멱등)
- 어느 회사인가 (`active()`):
  - skill 요청: data 핸들러 입구(`scoped`)가 `request.meta.tenant_id`를 그 요청 동안 정한다
    (contextvar. 그 요청에서 띄운 작업(asyncio task)도 같은 값을 잇는다)
  - 그 밖(수집기·키워드 풀·일간 스냅샷·campaign_update 같은 스스로 도는 루틴): 지금 회사
    `tenant.current()`
  - 별칭 `shop-demo`는 `catbooks`로, 없거나 맞지 않는 값은 캣북스. 쓰기는 늘 정식 id로 한다
- 회사 ID 없는 요청 거절은 하지 않는다(4단계)
"""

from __future__ import annotations

import contextvars
import functools
import sqlite3
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from ax_agent_template.companies import canonical_company_id
from ax_contracts.schemas.common import TENANT_ID_CATBOOKS

TENANT_COLUMN = "tenant_id"
CATBOOKS = TENANT_ID_CATBOOKS
_ACTIVE: contextvars.ContextVar[str | None] = contextvars.ContextVar("data_company", default=None)


def scope_id(company_id: str | None) -> str:
    """저장소가 쓸 정식 회사 ID. 없거나 맞지 않으면 캣북스."""
    return canonical_company_id(company_id) or CATBOOKS


def current_id() -> str:
    """지금 회사 (시스템 관리 › 회사 연결에서 고른 회사 폴더). 연결 전이면 캣북스."""
    from ax_agent_template import tenant

    return scope_id(tenant.current())


def active() -> str:
    """지금 쓰는 회사: skill 요청 안이면 그 요청의 회사, 아니면 지금 회사."""
    return _ACTIVE.get() or current_id()


def resolve(company_id: str | None) -> str:
    """저장소에 넘긴 값이 있으면 그 회사, 없으면 `active()`."""
    return scope_id(company_id) if company_id else active()


@contextmanager
def using(company_id: str | None) -> Iterator[str]:
    """이 블록 안에서 `active()`를 그 회사로."""
    cid = scope_id(company_id)
    token = _ACTIVE.set(cid)
    try:
        yield cid
    finally:
        _ACTIVE.reset(token)


def scoped(handler: Callable[[Any], Awaitable[Any]]) -> Callable[[Any], Awaitable[Any]]:
    """data skill 핸들러 감싸기: 요청 meta.tenant_id를 그 요청의 회사로."""

    @functools.wraps(handler)
    async def run(request: Any) -> Any:
        meta = getattr(request, "meta", None)
        with using(getattr(meta, "tenant_id", None)):
            return await handler(request)

    return run


# ---------------------------------------------------------------- 표


def _columns(conn: sqlite3.Connection, table: str) -> list[tuple[str, int]]:
    """(칸 이름, 기본 키 순서(0 = 키 아님))."""
    return [(r[1], r[5]) for r in conn.execute(f"PRAGMA table_info({table})")]  # noqa: S608


def ensure_tenant_column(conn: sqlite3.Connection, table: str) -> None:
    """옛 표에 회사 칸을 붙인다(기존 줄은 캣북스). 이미 있으면 안 한다. 호출자가 트랜잭션을 연다."""
    if TENANT_COLUMN not in {name for name, _ in _columns(conn, table)}:
        conn.execute(
            f"ALTER TABLE {table}"  # noqa: S608 - 상수 표 이름
            f" ADD COLUMN {TENANT_COLUMN} TEXT NOT NULL DEFAULT '{CATBOOKS}'"
        )


def rebuild_with_tenant_key(conn: sqlite3.Connection, table: str, create: str) -> bool:
    """기본 키에 회사가 들어간 표로 만든다. 다시 만들었으면 True.

    `create`는 `{table}` 자리에 표 이름을 넣는 CREATE TABLE 문(새 모양, 키에 tenant_id).
    - 표가 없으면 새 모양으로 만든다
    - 키에 tenant_id가 이미 있으면 건너뛴다(멱등)
    - 아니면 한 트랜잭션으로: `<표>__tenant`로 만들고 → 같은 이름 칸을 복사(회사 칸이 없던 줄은
      캣북스) → 옛 표를 지우고 → 이름을 바꾼다
    """
    old = _columns(conn, table)
    if not old:
        conn.execute(create.format(table=table))
        conn.commit()
        return False
    if any(name == TENANT_COLUMN and pk > 0 for name, pk in old):
        return False
    temp = f"{table}__tenant"
    names = [name for name, _ in old if name != TENANT_COLUMN]
    tenant_src = TENANT_COLUMN if TENANT_COLUMN in {n for n, _ in old} else f"'{CATBOOKS}'"
    cols = ", ".join(names)
    conn.commit()  # 열린 트랜잭션이 있으면 닫고 새로 연다
    try:
        conn.execute("BEGIN")
        conn.execute(f"DROP TABLE IF EXISTS {temp}")  # noqa: S608 - 상수 표 이름
        conn.execute(create.format(table=temp))
        conn.execute(
            f"INSERT INTO {temp} ({TENANT_COLUMN}, {cols})"  # noqa: S608 - 상수 표·칸 이름
            f" SELECT {tenant_src}, {cols} FROM {table}"
        )
        conn.execute(f"DROP TABLE {table}")  # noqa: S608
        conn.execute(f"ALTER TABLE {temp} RENAME TO {table}")  # noqa: S608
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return True


def key_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """기본 키 칸 (순서대로). 테스트·점검용."""
    got = sorted((pk, name) for name, pk in _columns(conn, table) if pk > 0)
    return [name for _, name in got]


def tenants(conn: sqlite3.Connection, table: str) -> Sequence[str]:
    """표에 있는 회사 ID들. 테스트·점검용."""
    rows = conn.execute(f"SELECT DISTINCT {TENANT_COLUMN} FROM {table}").fetchall()  # noqa: S608
    return sorted(r[0] for r in rows)
