"""감사 로그: hr이 한 일을 `context_id` 기준으로 남긴다(근거 점검·사후 확인의 재료).

DB(`audit_logs`)와 템플릿의 `log_event`(공통 로그)에 같이 남긴다.
"""

from __future__ import annotations

from typing import Any

from archive_agent.config import Clock
from archive_agent.store import Store
from archive_agent.util import dumps, loads
from ax_agent_template import company_scope
from ax_agent_template.logging import log_event
from ax_contracts.schemas import TENANT_ID_ALIASES, TENANT_ID_DEFAULT


class AuditLog:
    def __init__(self, store: Store, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    def record(
        self,
        context_id: str,
        kind: str,
        payload: dict[str, Any] | None = None,
        tenant_id: str | None = None,
    ) -> None:
        # 회사를 안 넘긴 곳이 많아서 요청 회사로 채운다(캣북스·요청 밖은 예전 값 그대로)
        if tenant_id is None:
            active = company_scope.active()
            tenant_id = active if active and active != company_scope.CATBOOKS else TENANT_ID_DEFAULT
        data = payload or {}
        self._store.execute(
            "INSERT INTO audit_logs(tenant_id, context_id, kind, payload, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (tenant_id, context_id, kind, dumps(data), self._clock().isoformat()),
        )
        log_event(context_id, tenant_id, kind, data)

    def list(self, context_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        where, args = _tenant_clause()
        if context_id:
            rows = self._store.all(
                f"SELECT * FROM audit_logs WHERE context_id = ?{where} ORDER BY id LIMIT ?",
                (context_id, *args, limit),
            )
        else:
            rows = self._store.all(
                f"SELECT * FROM audit_logs WHERE 1 = 1{where} ORDER BY id DESC LIMIT ?",
                (*args, limit),
            )
        for row in rows:
            row["payload"] = loads(row["payload"], {})
        return rows

    def kinds(self, context_id: str) -> list[str]:
        return [row["kind"] for row in self.list(context_id)]

    def contexts(self, limit: int = 50) -> list[dict[str, Any]]:
        """감사 기록이 있는 사건(`context_id`) 목록: 최근 기록이 있는 순서, 건수와 마지막 시각."""
        where, args = _tenant_clause()
        return self._store.all(
            "SELECT context_id, COUNT(*) AS entries, MIN(created_at) AS first_at,"
            f" MAX(created_at) AS last_at FROM audit_logs WHERE 1 = 1{where} GROUP BY context_id"
            " ORDER BY MAX(id) DESC LIMIT ?",
            (*args, limit),
        )

    def timeline(self, context_id: str) -> list[dict[str, Any]]:
        """한 사건에서 hr이 한 일을 시간순(기록 순)으로 돌려준다. 값은 기록된 그대로다."""
        rows = self.list(context_id, limit=1000)
        return [
            {"seq": n, "at": r["created_at"], "kind": r["kind"], "payload": r["payload"]}
            for n, r in enumerate(rows, start=1)
        ]


def _tenant_clause() -> tuple[str, tuple[str, ...]]:
    """캣북스 요청이면 캣북스 기록만(옛 이름 `shop-demo` 포함). 파일은 회사마다 따로지만(#1773) 분리
    전에 캣북스 파일에 쌓인 다른 회사 기록을 거른다. 다른 회사·요청 밖은 거르지 않는다."""
    company = company_scope.active()
    if company != company_scope.CATBOOKS:  # 다른 회사 파일은 그 회사 기록만 있다(#1773)
        return "", ()
    names = (company, *(old for old, new in TENANT_ID_ALIASES.items() if new == company))
    return f" AND tenant_id IN ({', '.join('?' for _ in names)})", names
