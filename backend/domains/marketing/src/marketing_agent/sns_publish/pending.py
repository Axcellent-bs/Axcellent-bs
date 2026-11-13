"""게시 대기 저장소와 상태 기계 (SQLite, `briefs.py`와 같은 방식).

    held ──(승인 확인 + execute 재생 executed)──▶ approved ──(선점)──▶ publishing ──▶ published
      │                                                                    └────────▶ failed
      └──(거절·만료)──▶ rejected             (held·approved에서도 내용이 바뀌면 failed)

- 전이는 `UPDATE ... WHERE status = <이전 상태>`(compare-and-swap) 한 번으로만 한다. 이벤트가 두 번
  와도, 이벤트와 폴링이 겹쳐도 `approved → publishing`을 이기는 쪽은 하나뿐이다(락 역할).
- `publishing` 이상이면 재진입하지 않는다. 프로세스가 `publishing`에서 죽으면
  `recover_interrupted`가 `media_publish`를 보냈는지(`publish_sent`)를 보고, 보냈으면 컨테이너
  상태로 판정하고 안 보냈으면 `failed`로 닫는다(어느 쪽도 다시 게시하지 않는다).
- 토큰·서명 URL 원문은 저장하지 않는다. `asset_ref`는 내용 주소 참조(경로·ID)만 둔다.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

DB_ENV = "MARKETING_SNS_PENDING_DB"

HELD, APPROVED, PUBLISHING = "held", "approved", "publishing"
PUBLISHED, FAILED, REJECTED = "published", "failed", "rejected"
STATUSES = (HELD, APPROVED, PUBLISHING, PUBLISHED, FAILED, REJECTED)
# 허용되는 전이. 이 밖은 막는다
TRANSITIONS: dict[str, tuple[str, ...]] = {
    HELD: (APPROVED, REJECTED, FAILED),
    APPROVED: (PUBLISHING, FAILED),
    PUBLISHING: (PUBLISHED, FAILED),
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sns_pending (
    idempotency_key TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    context_id      TEXT NOT NULL,
    post_draft_id   TEXT NOT NULL,
    content_sha     TEXT NOT NULL,
    execution_id    TEXT,
    review_id       TEXT UNIQUE,
    status          TEXT NOT NULL,
    params          TEXT NOT NULL,
    failure_code    TEXT,
    container_id    TEXT,
    publish_sent    INTEGER NOT NULL DEFAULT 0,
    media_id        TEXT,
    result          TEXT,
    attempts        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sns_halt (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    failure_code    TEXT NOT NULL,
    idempotency_key TEXT,
    created_at      TEXT NOT NULL
);
"""
_UPDATABLE = frozenset(
    {
        "status",
        "execution_id",
        "review_id",
        "failure_code",
        "container_id",
        "publish_sent",
        "media_id",
        "result",
        "attempts",
    }
)


class PendingStorageError(Exception):
    """대기 저장소를 읽거나 쓰지 못했다."""


@dataclass
class PendingPost:
    idempotency_key: str
    tenant_id: str
    context_id: str
    post_draft_id: str
    content_sha: str
    execution_id: str | None
    review_id: str | None
    status: str
    params: dict[str, Any]
    failure_code: str | None = None
    container_id: str | None = None
    publish_sent: bool = False
    media_id: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    attempts: int = 0


_COLUMNS = (
    "idempotency_key, tenant_id, context_id, post_draft_id, content_sha, execution_id, review_id,"
    " status, params, failure_code, container_id, publish_sent, media_id, result, attempts"
)


def _row(row: tuple) -> PendingPost:
    return PendingPost(
        idempotency_key=row[0],
        tenant_id=row[1],
        context_id=row[2],
        post_draft_id=row[3],
        content_sha=row[4],
        execution_id=row[5],
        review_id=row[6],
        status=row[7],
        params=json.loads(row[8]),
        failure_code=row[9],
        container_id=row[10],
        publish_sent=bool(row[11]),
        media_id=row[12],
        result=json.loads(row[13]) if row[13] else {},
        attempts=row[14],
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()


class PendingStore:
    """`path`가 없으면 `MARKETING_SNS_PENDING_DB`, 그것도 없으면 메모리."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path if path is not None else (os.getenv(DB_ENV, "").strip() or ":memory:")
        self._lock = threading.Lock()
        try:
            self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
            with self._lock, self._conn:
                self._conn.executescript(_SCHEMA)
        except sqlite3.Error as error:
            raise PendingStorageError(
                f"게시 대기 저장소를 열 수 없다: {type(error).__name__}"
            ) from error

    def close(self) -> None:
        self._conn.close()

    def _query(self, where: str, args: tuple = ()) -> list[PendingPost]:
        try:
            with self._lock:
                rows = self._conn.execute(
                    f"SELECT {_COLUMNS} FROM sns_pending {where}",  # noqa: S608
                    args,
                ).fetchall()
        except sqlite3.Error as error:
            raise PendingStorageError(f"대기 건을 읽을 수 없다: {type(error).__name__}") from error
        return [_row(r) for r in rows]

    def get(self, key: str) -> PendingPost | None:
        found = self._query("WHERE idempotency_key = ?", (key,))
        return found[0] if found else None

    def by_review(self, review_id: str) -> PendingPost | None:
        found = self._query("WHERE review_id = ?", (review_id,))
        return found[0] if found else None

    def with_status(self, status: str) -> list[PendingPost]:
        return self._query("WHERE status = ? ORDER BY created_at", (status,))

    def with_content_sha(self, sha: str) -> list[PendingPost]:
        """같은 내용(content_sha)의 모든 대기 건(재요청 거부 판단용)."""
        return self._query("WHERE content_sha = ? ORDER BY created_at", (sha,))

    def set_review_expiry(self, key: str, expires_at: str) -> None:
        """held인 건에만 안전 관문 마감 시각을 `result.review_expires_at`에 적는다(상태 불변)."""
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "UPDATE sns_pending SET result = json_set(COALESCE(result, '{}'),"
                    " '$.review_expires_at', ?) WHERE idempotency_key = ? AND status = ?",
                    (expires_at, key, HELD),
                )
        except sqlite3.Error as error:
            raise PendingStorageError(
                f"마감 시각을 적을 수 없다: {type(error).__name__}"
            ) from error

    def count_posts_since(self, since: datetime) -> int:
        """`since` 이후 게시된(또는 `media_publish`를 보낸) 건수. 게시 여부가 불확실한 건도 센다."""
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM sns_pending WHERE updated_at >= ?"
                    " AND (status = ? OR publish_sent = 1)",
                    (since.astimezone(UTC).isoformat(), PUBLISHED),
                ).fetchone()
        except sqlite3.Error as error:
            raise PendingStorageError(
                f"게시 건수를 읽을 수 없다: {type(error).__name__}"
            ) from error
        return int(row[0])

    def halt_set(self, failure_code: str, key: str | None = None) -> None:
        """중단 기록을 남긴다(`publish_halted`). 해제는 설정 파일의 `halt_cleared_before`."""
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT INTO sns_halt (failure_code, idempotency_key, created_at)"
                    " VALUES (?,?,?)",
                    (failure_code, key, _now()),
                )
        except sqlite3.Error as error:
            raise PendingStorageError(
                f"중단 기록을 저장할 수 없다: {type(error).__name__}"
            ) from error

    def halt_latest(self) -> tuple[str, datetime] | None:
        """가장 최근 중단 기록 (실패 코드, 시각). 없으면 None."""
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT failure_code, created_at FROM sns_halt ORDER BY id DESC LIMIT 1"
                ).fetchone()
        except sqlite3.Error as error:
            raise PendingStorageError(
                f"중단 기록을 읽을 수 없다: {type(error).__name__}"
            ) from error
        return (row[0], datetime.fromisoformat(row[1])) if row else None

    def create(self, post: PendingPost) -> bool:
        """같은 키가 이미 있으면 False(먼저 들어간 것이 이긴다)."""
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT INTO sns_pending (idempotency_key, tenant_id, context_id,"
                    " post_draft_id, content_sha, execution_id, review_id, status, params,"
                    " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        post.idempotency_key,
                        post.tenant_id,
                        post.context_id,
                        post.post_draft_id,
                        post.content_sha,
                        post.execution_id,
                        post.review_id,
                        post.status,
                        json.dumps(post.params, ensure_ascii=False),
                        _now(),
                        _now(),
                    ),
                )
        except sqlite3.IntegrityError:
            return False
        except sqlite3.Error as error:
            raise PendingStorageError(
                f"대기 건을 저장할 수 없다: {type(error).__name__}"
            ) from error
        return True

    def transition(self, key: str, old: str, new: str, **fields: Any) -> bool:
        """`old`일 때만 `new`로 바꾼다. 이긴 쪽만 True. 허용되지 않는 전이는 ValueError."""
        if new not in TRANSITIONS.get(old, ()):
            raise ValueError(f"허용되지 않는 전이: {old} → {new}")
        return self._update(key, old, status=new, **fields)

    def update(self, key: str, **fields: Any) -> None:
        """상태를 바꾸지 않고 기록 필드만 갱신한다(container_id·publish_sent·attempts·result)."""
        if "status" in fields:
            raise ValueError("상태는 transition으로만 바꾼다")
        self._update(key, None, **fields)

    def _update(self, key: str, expect: str | None, **fields: Any) -> bool:
        unknown = set(fields) - _UPDATABLE
        if unknown:
            raise ValueError(f"알 수 없는 필드: {sorted(unknown)}")
        values: dict[str, Any] = {}
        for name, value in fields.items():
            if name == "result":
                value = json.dumps(value, ensure_ascii=False)
            elif name == "publish_sent":
                value = int(bool(value))
            values[name] = value
        values["updated_at"] = _now()
        sets = ", ".join(f"{name} = ?" for name in values)  # 이름은 위 허용 목록에서만 온다
        where, args = "idempotency_key = ?", [key]
        if expect is not None:
            where += " AND status = ?"
            args.append(expect)
        try:
            with self._lock, self._conn:
                cur = self._conn.execute(
                    f"UPDATE sns_pending SET {sets} WHERE {where}",  # noqa: S608
                    (*values.values(), *args),
                )
        except sqlite3.Error as error:
            raise PendingStorageError(
                f"대기 건을 갱신할 수 없다: {type(error).__name__}"
            ) from error
        return cur.rowcount == 1
