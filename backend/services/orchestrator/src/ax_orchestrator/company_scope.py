"""총괄 저장소의 회사 칸 도우미 (여러 회사 설계 1-6, 2026-11-23).

- 저장소 표마다 `tenant_id`(회사 ID) 칸을 둔다. 옛 DB는 켤 때 `ALTER TABLE ... ADD COLUMN
  tenant_id TEXT NOT NULL DEFAULT 'catbooks'`로 칸을 붙여 기존 줄이 캣북스가 된다(멱등: 칸이 이미
  있으면 건너뛴다).
- 저장소는 만들 때 회사 ID(`company_id`, 기본 캣북스)를 받아 쓰기에 그 값을 적고,
  읽기·고치기·지우기에 `tenant_id = ?`로 거른다. 별칭 `shop-demo`는 `catbooks`로 바꾸고,
  맞지 않는 값은 캣북스로 둔다.
- 회사 ID 없는 요청 거절은 하지 않는다(4단계). 지금 동작은 그대로다(회사가 캣북스 하나).
- 기본키(`context_id`·`event_id`·`request_id` 등)는 그대로 둔다. 회사마다 같은 ID가 겹치는 일은
  회사 ID를 강제하는 5단계에서 다룬다.
"""

from __future__ import annotations

import sqlite3

from ax_agent_template.companies import canonical_company_id
from ax_contracts.schemas.common import TENANT_ID_CATBOOKS

TENANT_COLUMN = "tenant_id"


def scope_id(company_id: str | None) -> str:
    """저장소가 쓸 정식 회사 ID. 없거나 맞지 않으면 캣북스."""
    return canonical_company_id(company_id) or TENANT_ID_CATBOOKS


def ensure_tenant_column(conn: sqlite3.Connection, table: str) -> None:
    """옛 표에 회사 칸을 붙인다(기존 줄은 캣북스). 이미 있으면 안 한다. 호출자가 커밋한다."""
    have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}  # noqa: S608 - 상수 표 이름
    if TENANT_COLUMN not in have:
        conn.execute(
            f"ALTER TABLE {table}"  # noqa: S608 - 상수 표 이름
            f" ADD COLUMN {TENANT_COLUMN} TEXT NOT NULL DEFAULT '{TENANT_ID_CATBOOKS}'"
        )
