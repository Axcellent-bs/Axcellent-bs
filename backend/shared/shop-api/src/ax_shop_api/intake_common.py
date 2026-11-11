"""문의함·클레임함이 함께 쓰는 실패 처리: 재시도 간격·상한, 실패로 빼기, 막힌 접수 목록, 다시 시도.

두 함의 규칙이 따로 갈라지지 않게(예진 님 리뷰 10번) 한 곳에 둔다.
표 이름은 코드 상수만 넘긴다(고객 입력이 아니다).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from ax_shop_api.db import Db

MAX_ATTEMPTS_DEFAULT = 6
BACKOFF_BASE_SECONDS = 60
BACKOFF_MAX_SECONDS = 1800


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def now() -> str:
    return stamp(datetime.now(UTC))


def max_attempts() -> int:
    """실패가 이 횟수에 닿으면 실패로 뺀다(6이면 여섯 번째 실패에서)."""
    try:
        value = int(os.getenv("SHOP_INBOX_MAX_ATTEMPTS") or MAX_ATTEMPTS_DEFAULT)
    except ValueError:
        return MAX_ATTEMPTS_DEFAULT
    return value if value > 0 else MAX_ATTEMPTS_DEFAULT


def backoff_seconds(attempts: int) -> int:
    """실패 attempts번째 뒤 기다리는 초. 1분, 2분, 4분… 최대 30분."""
    return min(BACKOFF_BASE_SECONDS * 2 ** max(attempts - 1, 0), BACKOFF_MAX_SECONDS)


ExhaustedSets = Callable[[str], dict[str, Any]]


def report_failure(
    db: Db, table: str, seq: int, reason: str, exhausted: ExhaustedSets | None = None
) -> dict[str, Any] | None:
    """읽어 간 쪽이 처리하지 못했다고 알린다. 다시 읽는 시각을 늦추고 횟수에 닿으면 실패로 뺀다.

    이미 처리된(synced_at) 접수는 그대로 둔다. 없는 번호면 None이다. reason은 짧은 코드다.
    exhausted는 실패로 뺄 때 함께 바꿀 칸(클레임은 고객에게 접수 불가로 닫는다)을 시각으로 만든다.
    """
    with db.tx() as conn:
        row = conn.execute(f"SELECT * FROM {table} WHERE seq = ?", (seq,)).fetchone()  # noqa: S608
        if row is None:
            return None
        if row["synced_at"] is None and row["failed_at"] is None:
            attempts = row["attempts"] + 1
            moment = now()
            if attempts >= max_attempts():
                sets: dict[str, Any] = {"attempts": attempts, "failed_at": moment}
                sets |= {"retry_after": None, "last_error": reason[:40]}
                sets |= exhausted(moment) if exhausted else {}
            else:
                later = stamp(datetime.now(UTC) + timedelta(seconds=backoff_seconds(attempts)))
                sets = {"attempts": attempts, "retry_after": later, "last_error": reason[:40]}
            columns = ", ".join(f"{name} = ?" for name in sets)
            conn.execute(
                f"UPDATE {table} SET {columns} WHERE seq = ?",  # noqa: S608
                (*sets.values(), seq),
            )
            row = conn.execute(f"SELECT * FROM {table} WHERE seq = ?", (seq,)).fetchone()  # noqa: S608
    return {
        "seq": row["seq"],
        "attempts": row["attempts"],
        "failed": row["failed_at"] is not None,
        "retry_after": row["retry_after"],
    }


def stuck_rows(db: Db, table: str, kind: str, where: str) -> list[dict[str, Any]]:
    """실패로 빼 둔 접수(사람이 봐야 한다). 고객이 쓴 글은 싣지 않고 번호·사유·횟수만 돌려준다."""
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT seq, digest, attempts, last_error, failed_at, created_at"  # noqa: S608
            f" FROM {table} WHERE {where} ORDER BY seq"
        ).fetchall()
    return [
        {
            "kind": kind,
            "seq": r["seq"],
            "digest": r["digest"],
            "reason": r["last_error"],
            "attempts": r["attempts"],
            "failed_at": r["failed_at"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def requeue(
    db: Db, table: str, seq: int, where: str, sets: dict[str, Any], allowed: Callable[[Any], bool]
) -> dict[str, Any] | None:
    """실패로 뺀 접수를 다시 읽는 차례로 돌린다. 없는 번호면 None, 돌릴 수 없으면 requeued=False.

    where는 실패로 빼 둔 접수의 조건(막힌 접수 목록과 같다), allowed는 그 밖의 제한(이미 새로
    접수된 신청이면 안 된다 등)이다.
    """
    with db.tx() as conn:
        row = conn.execute(f"SELECT * FROM {table} WHERE seq = ?", (seq,)).fetchone()  # noqa: S608
        if row is None:
            return None
        hit = conn.execute(
            f"SELECT 1 FROM {table} WHERE seq = ? AND {where}",  # noqa: S608
            (seq,),
        ).fetchone()
        done = hit is not None and allowed(row)
        if done:
            base = {"attempts": 0, "failed_at": None, "retry_after": None}
            merged = base | sets
            columns = ", ".join(f"{name} = ?" for name in merged)
            conn.execute(
                f"UPDATE {table} SET {columns} WHERE seq = ?",  # noqa: S608
                (*merged.values(), seq),
            )
    return {"seq": seq, "requeued": done}
