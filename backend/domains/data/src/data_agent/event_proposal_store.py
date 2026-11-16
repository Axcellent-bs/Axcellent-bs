"""이벤트 제안 저장소 (data 자체 SQLite 파일, 커밋하지 않음).

`event_proposal`이 만든 제안을 저장해 두고, `event_proposal_send`(화면 버튼)가 proposal_id로 찾아
기획에 보낸다. 보낸 결과(기획 제안 번호·시각·결과)도 여기에 남겨 다음 `event_proposal` 응답의
`sent`로 보인다.

- 위치: 환경변수 `DATA_EVENT_PROPOSAL_DB`(없으면 `backend/.data/event_proposals.db`)
- 고객사(tenant_id)마다 따로. (tenant_id, proposal_id)가 기본 키
- 같은 proposal_id를 다시 저장하면 제안 내용(body·상품·문장)만 바꾸고 보낸 기록은 그대로 둔다
- `facts_key`: 제목·요약을 쓴 숫자의 지문. 같으면 LLM 문장을 다시 쓰지 않는다
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ax_contracts.schemas.data import EventProposalItem, ProposalSent

DB_ENV = "DATA_EVENT_PROPOSAL_DB"
DEFAULT_DB = Path(__file__).resolve().parents[4] / ".data" / "event_proposals.db"  # backend/.data

_SCHEMA = """
CREATE TABLE IF NOT EXISTS event_proposals (
    tenant_id      TEXT NOT NULL,
    proposal_id    TEXT NOT NULL,
    month          TEXT NOT NULL,
    body           TEXT NOT NULL,      -- EventProposalItem JSON (sent·past_results 제외)
    products       TEXT NOT NULL,      -- [[product_id, 이름], ...] 그 장르 상위 상품
    facts_key      TEXT,               -- 제목·요약을 쓴 숫자의 지문
    text_by_llm    INTEGER NOT NULL DEFAULT 0,  -- 제목·요약이 LLM 문장(검증 통과)이면 1
    sent_at        TEXT,
    sent_outcome   TEXT,               -- created | duplicate | rejected
    sent_ref       TEXT,               -- 기획 제안 번호 (PROP-n)
    updated_at     TEXT NOT NULL,
    PRIMARY KEY (tenant_id, proposal_id)
);
"""


@dataclass
class StoredProposal:
    item: EventProposalItem  # sent는 저장된 보낸 기록으로 채운다
    products: list[tuple[str, str]] = field(default_factory=list)
    facts_key: str | None = None
    text_by_llm: bool = False


class EventProposalStore:
    """SQLite 파일(또는 `:memory:`) 저장소. 연결 하나를 잠금으로 보호한다."""

    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def save(
        self,
        tenant_id: str,
        stored: StoredProposal,
    ) -> None:
        """제안 내용을 덮어쓴다. 보낸 기록(sent_*)은 바꾸지 않는다."""
        item = stored.item
        body = item.model_dump_json(exclude={"sent", "past_results"})
        products = json.dumps([list(p) for p in stored.products], ensure_ascii=False)
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO event_proposals (tenant_id, proposal_id, month, body, products,"
                " facts_key, text_by_llm, updated_at) VALUES (?,?,?,?,?,?,?,?)"
                " ON CONFLICT(tenant_id, proposal_id) DO UPDATE SET month = excluded.month,"
                " body = excluded.body, products = excluded.products,"
                " facts_key = excluded.facts_key, text_by_llm = excluded.text_by_llm,"
                " updated_at = excluded.updated_at",
                (tenant_id, item.proposal_id, item.timing_basis.month, body, products,
                 stored.facts_key, int(stored.text_by_llm), now),
            )  # fmt: skip

    def mark_sent(self, tenant_id: str, proposal_id: str, sent: ProposalSent) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE event_proposals SET sent_at = ?, sent_outcome = ?, sent_ref = ?,"
                " updated_at = ? WHERE tenant_id = ? AND proposal_id = ?",
                (sent.sent_at.isoformat(), sent.outcome, sent.proposal_ref,
                 datetime.now(UTC).isoformat(), tenant_id, proposal_id),
            )  # fmt: skip

    def get(self, tenant_id: str, proposal_id: str) -> StoredProposal | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM event_proposals WHERE tenant_id = ? AND proposal_id = ?",
                (tenant_id, proposal_id),
            ).fetchone()
        return None if row is None else _stored(row)

    def all(self, tenant_id: str) -> list[StoredProposal]:
        """그 고객사의 저장된 제안 전부 (달·id 순)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM event_proposals WHERE tenant_id = ? ORDER BY month, proposal_id",
                (tenant_id,),
            ).fetchall()
        return [_stored(r) for r in rows]


def _stored(row: sqlite3.Row) -> StoredProposal:
    item = EventProposalItem.model_validate_json(row["body"])
    if row["sent_at"]:
        item = item.model_copy(update={"sent": ProposalSent(
            sent_at=datetime.fromisoformat(row["sent_at"]),
            outcome=row["sent_outcome"],
            proposal_ref=row["sent_ref"],
        )})  # fmt: skip
    return StoredProposal(
        item=item,
        products=[(str(p[0]), str(p[1])) for p in json.loads(row["products"])],
        facts_key=row["facts_key"],
        text_by_llm=bool(row["text_by_llm"]),
    )
