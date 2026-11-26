"""장애 상세의 "확정 원인": 사람이 적어 저장한 실제 원인 (SPEC-30 R1·R3, 11/25).

장애 대응 탭의 장애 상세(`/infra/{id}`)는 DejaFix `GET /api/incidents/{id}`의 리포트·원인 후보를
옮겨 보여 줄 뿐이다. 원인 후보는 DejaFix AI의 추정이라 사람이 확정하기 전에는 "확정 아님"이다.
운영자가 실제 원인을 적어 저장하는 기록은 AXcellent 쪽에 둔다. DejaFix에는 쓰지 않는다
(DejaFix 쓰기 API를 부르지 않는다).

- `GET  /api/dashboard/incidents/{incident_id}/cause`: 지금 확정 원인(`confirmed`, 없으면 null)과
  앞서 저장했다가 바뀐 기록(`history`, 최근 것부터 최대 20줄). 사람 토큰이면 누구나
- `POST /api/dashboard/incidents/{incident_id}/cause` `{text, from_rank?}`: 확정 원인 저장.
  로그인 세션만 받는다(누가 저장했는지 남겨야 해서. 공용 토큰은 누가 쓰는지 몰라 403).
  다시 저장하면 지금 것을 바꾸고 앞의 것은 `history`로 남긴다(지우지 않는다).
  `text`는 앞뒤 공백을 떼고 1~1000자. `from_rank`는 원인 후보에서 시작했으면 그 순위(1~20)
- 회사: 요청 회사 = 로그인 세션의 회사(세션이 없으면 캣북스, `office_room.request_company`와 같다).
  같은 장애 번호라도 회사마다 따로 저장하고, 다른 회사 기록은 읽지도 덮지도 않는다.
  장애 번호는 그 회사가 연결한 DejaFix의 번호다
- 사람이 화면에서 남기는 기록이라 agent-template `execute()`(AI 상태 변경 관문)를 거치지 않는다
  (CLAUDE.md 백엔드 규칙의 사람 설정 변경 예외와 같다). AI가 이 값을 쓰는 경로는 없다
- 장애 번호가 DejaFix에 있는지는 여기서 확인하지 않는다(화면은 DejaFix 상세를 읽은 뒤에만 저장
  칸을 보인다). 없는 번호로 저장해도 그 회사 기록에만 남는다

읽기 전용 창구(11/26): 인프라 AI의 1:1 대화가 사람이 확정한 원인을 알도록
`GET /api/dashboard/incident-causes?ids=1,2` (`machine_router`). 사람 토큰이 아니라 **인프라
에이전트 토큰**(`AX_AGENT_TOKENS`의 `infra`)만 받고(사람·다른 에이전트는 403), 회사는
`X-AX-Company` 머리글로 받는다(없거나 규칙에 안 맞으면 400, 캣북스로 떨어지지 않는다).
지금 확정 원인(`text`·`from_rank`·`saved_by`·`saved_at`)만 주고 history·계정 ID는 주지 않는다.
쓰기 경로는 없다. 사람 토큰 라우터(`router`)는 그대로다.

저장은 SQLite 파일 하나다. 위치는 `AX_INCIDENT_CAUSES_DB`이고, 비어 있으면 프로세스 메모리
(`:memory:`)에 둔다(다시 켜면 사라진다). run_all.sh는 `.data/incident_causes.db`로 정한다.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from ax_agent_template.companies import canonical_company_id
from ax_agent_template.company_connection import CALLER_COMPANY_HEADER
from ax_agent_template.company_scope import scope_id
from ax_agent_template.server import verify_token
from ax_dashboard_api import accounts
from ax_dashboard_api.auth import bearer, require_human
from ax_dashboard_api.envelope import ApiError, ok, session_company

DB_ENV = "AX_INCIDENT_CAUSES_DB"
TEXT_MAX = 1000
RANK_MAX = 20
HISTORY_MAX = 20
INCIDENT_ID_MAX = 2**31 - 1
IDS_MAX = 20
READER_AGENTS = frozenset({"infra"})  # 읽기 창구를 쓸 수 있는 에이전트

router = APIRouter(prefix="/api/dashboard/incidents", dependencies=[Depends(require_human)])

# 에이전트가 읽는 창구: 사람 토큰 대신 에이전트 토큰(auth.MACHINE_ROUTES)
machine_router = APIRouter(prefix="/api/dashboard/incident-causes")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS incident_causes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id   TEXT NOT NULL,
    incident_id INTEGER NOT NULL,
    text        TEXT NOT NULL,
    from_rank   INTEGER,
    saved_by    TEXT NOT NULL,
    account_id  TEXT,
    saved_at    TEXT NOT NULL,
    replaced_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_incident_causes_key ON incident_causes (tenant_id, incident_id);
"""
_PUBLIC = ("text", "from_rank", "saved_by", "saved_at")


class CauseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=TEXT_MAX * 2)  # 앞뒤 공백을 뗀 뒤 길이는 아래에서 본다
    from_rank: int | None = Field(default=None, ge=1, le=RANK_MAX)


class IncidentCauseStore:
    def __init__(self, path: str) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def state(self, tenant: str, incident_id: int) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM incident_causes WHERE tenant_id = ? AND incident_id = ?"
                " ORDER BY id DESC LIMIT ?",
                (tenant, incident_id, HISTORY_MAX + 1),
            ).fetchall()
        current = next((r for r in rows if r["replaced_at"] is None), None)
        history = [r for r in rows if r["replaced_at"] is not None][:HISTORY_MAX]
        return {
            "incident_id": incident_id,
            "confirmed": _public(current) if current else None,
            "history": [_public(r) | {"replaced_at": r["replaced_at"]} for r in history],
        }

    def confirmed_many(self, tenant: str, incident_ids: list[int]) -> dict[int, dict[str, Any]]:
        """그 회사의 지금 확정 원인만(없는 번호는 빠진다)."""
        if not incident_ids:
            return {}
        marks = ",".join("?" for _ in incident_ids)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM incident_causes WHERE tenant_id = ? AND replaced_at IS NULL"
                f" AND incident_id IN ({marks})",
                (tenant, *incident_ids),
            ).fetchall()
        return {r["incident_id"]: _public(r) for r in rows}

    def save(
        self,
        tenant: str,
        incident_id: int,
        *,
        text: str,
        from_rank: int | None,
        saved_by: str,
        account_id: str | None,
        now: datetime,
    ) -> None:
        """지금 것을 바꾼 것으로 표시하고 새 줄을 넣는다(한 장애에 지금 것은 하나)."""
        at = now.isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE incident_causes SET replaced_at = ?"
                " WHERE tenant_id = ? AND incident_id = ? AND replaced_at IS NULL",
                (at, tenant, incident_id),
            )
            self._conn.execute(
                "INSERT INTO incident_causes (tenant_id, incident_id, text, from_rank, saved_by,"
                " account_id, saved_at) VALUES (?,?,?,?,?,?,?)",
                (tenant, incident_id, text, from_rank, saved_by, account_id, at),
            )


def _public(row: sqlite3.Row) -> dict[str, Any]:
    """화면에 주는 칸. 계정 ID는 감사용으로만 두고 내보내지 않는다."""
    return {k: row[k] for k in _PUBLIC}


_store: IncidentCauseStore | None = None
_store_key: str | None = None
_store_lock = threading.Lock()


def get_store() -> IncidentCauseStore:
    """`AX_INCIDENT_CAUSES_DB`가 바뀌면 새로 연다. 비어 있으면 메모리 저장소."""
    global _store, _store_key
    path = os.getenv(DB_ENV) or ":memory:"
    with _store_lock:
        if _store is None or _store_key != path:
            _store = IncidentCauseStore(path)
            _store_key = path
        return _store


def reset_store() -> None:
    """테스트 초기화용."""
    global _store, _store_key
    with _store_lock:
        _store, _store_key = None, None


def _now() -> datetime:
    return datetime.now(UTC)


IncidentId = Path(ge=1, le=INCIDENT_ID_MAX)


@router.get("/{incident_id}/cause")
def get_incident_cause(incident_id: int = IncidentId) -> dict[str, Any]:
    return ok(get_store().state(scope_id(session_company()), incident_id))


@router.post("/{incident_id}/cause")
def save_incident_cause(
    body: CauseIn,
    incident_id: int = IncidentId,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    human = accounts.session_human(authorization)  # 누가 저장했는지: 로그인 계정만
    text = body.text.strip()
    if not text:
        raise ApiError(422, "CAUSE_EMPTY", "확정 원인을 적어 주세요")
    if len(text) > TEXT_MAX:
        raise ApiError(422, "CAUSE_TOO_LONG", f"확정 원인은 {TEXT_MAX}자까지 적을 수 있어요")
    tenant = scope_id(human.company_id)
    store = get_store()
    store.save(
        tenant,
        incident_id,
        text=text,
        from_rank=body.from_rank,
        saved_by=human.name,
        account_id=human.account_id,
        now=_now(),
    )
    return ok(store.state(tenant, incident_id))


def _reader_company(authorization: str | None, company_header: str | None) -> str:
    """에이전트 토큰(허용된 이름만)과 호출 회사를 확인해 정식 회사 id를 돌려준다."""
    token = bearer(authorization)
    if not token:
        raise ApiError(
            401, "AUTH_REQUIRED", "에이전트 토큰이 필요하다 (Authorization: Bearer 토큰)"
        )
    try:
        agent = verify_token(token)
    except PermissionError:
        raise ApiError(403, "AGENT_TOKEN_INVALID", "에이전트 토큰을 검증할 수 없다") from None
    if agent not in READER_AGENTS:
        raise ApiError(403, "AGENT_NOT_ALLOWED", "이 에이전트는 확정 원인을 읽을 수 없다")
    company = canonical_company_id((company_header or "").strip())
    if not company:
        raise ApiError(400, "COMPANY_REQUIRED", f"{CALLER_COMPANY_HEADER} 머리글이 필요하다")
    return scope_id(company)


@machine_router.get("")
def read_incident_causes(
    ids: str = Query(max_length=200),
    authorization: str | None = Header(None),
    company: str | None = Header(None, alias=CALLER_COMPANY_HEADER),
) -> dict[str, Any]:
    """읽기 전용. `ids`는 쉼표로 구분한 장애 번호(최대 20개). 확정 원인이 있는 번호만 준다."""
    tenant = _reader_company(authorization, company)
    wanted: list[int] = []
    try:
        for part in ids.split(","):
            n = int(part.strip())
            if not 1 <= n <= INCIDENT_ID_MAX:
                raise ValueError
            if n not in wanted:
                wanted.append(n)
    except ValueError:
        raise ApiError(422, "IDS_INVALID", "ids는 쉼표로 구분한 장애 번호여야 한다") from None
    if len(wanted) > IDS_MAX:
        raise ApiError(422, "IDS_TOO_MANY", f"한 번에 {IDS_MAX}개까지 읽을 수 있다")
    found = get_store().confirmed_many(tenant, wanted)
    return ok({str(i): found[i] for i in wanted if i in found})
