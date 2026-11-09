"""같은 LLM 요청이 동시에 여러 번 나가지 않게 하는 "계산 중" 표시.

11-08 0시: 화면 요청과 수집기의 미리 계산이 같은 분류 요청을 동시에 보내 17회·83,886토큰이 겹쳤다.

- 같은 요청 = 모델·응답 형식(스키마)·프롬프트·데이터 블록이 같음 (`request_key`). 이미지가
  붙은 요청은 묶지 않는다
- 먼저 온 쪽이 표시(주인 이름 포함)를 남기고(`claim`) LLM을 부른다. 끝나면 답을 주인 이름과 같이
  남기고 표시를 지운다 (한 번에)
- 표시를 본 요청은 직접 부르지 않고 기다렸다가, **기다리면서 본 주인이 남긴 답**을 받는다.
  시각으로 가르지 않는다 (Windows 시계는 15ms 단위라 끝난 직후에 따로 온 요청과 구별이 안 됨)
  - 끝난 뒤에 따로 온 같은 요청은 주인을 본 적이 없으니 예전처럼 새로 부른다
    (저장 재사용은 각 skill의 저장분이 맡는다)
  - 답을 확인한 직후 주인이 끝나 표시가 비면, 표시를 남긴 뒤 그 주인의 답을 한 번 더 본다
- 먼저 부른 쪽이 실패하면 답 없이 표시만 지운다 → 기다리던 쪽 하나가 표시를 남기고 대신 부른다
- 표시를 남긴 프로세스가 죽으면 표시가 만료(`DATA_LLM_INFLIGHT_SECONDS`, 기본 120초)된 뒤
  대신 부른다
  - 120초 = LLM 요청 하나의 응답 제한 60초(`LLM_TIMEOUT_SEC` 기본) × 보내는 횟수 2(형식 재시도 1번
    포함, `LLM_MAX_RETRIES` 기본 1). 살아 있는 호출이 이보다 길게 걸릴 수는 없다
  - 평소 소요 시간(미리 계산의 연속 호출 간격, 11-05~11-09 310회): 중앙값 7.6초, p95 25.8초,
    p99 41초, 최대 100.5초(호출 사이 다른 계산 포함)
- 어디에: 서버·수집기가 같이 보도록 `llm_budget`과 같은 SQLite 파일(`DATA_LLM_BUDGET_DB`).
  없으면 프로세스 메모리 (테스트)
- 기다린 요청은 LLM을 부르지 않으므로 하루 셈(`llm_budget`)에 더하지 않는다
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Collection
from pathlib import Path
from typing import Any

from data_agent.llm_budget import DB_ENV

LEASE_ENV = "DATA_LLM_INFLIGHT_SECONDS"
DEFAULT_LEASE = 120.0  # 60초 응답 제한 × 2번 (위 설명)
POLL_SECONDS = 0.2
KEEP_SECONDS = 600.0  # 남긴 답은 이만큼 뒤에 지운다 (기다리던 쪽은 0.2초 안에 읽는다)


def request_key(model: str | None, schema: Any, prompt: str, untrusted: Any) -> str:
    """같은 요청인지 가르는 키. 값은 해시만 남는다 (프롬프트·데이터는 저장하지 않음)."""
    body = json.dumps(
        [model, getattr(schema, "__name__", str(schema)),
         schema.model_json_schema() if hasattr(schema, "model_json_schema") else None,
         prompt, untrusted],
        ensure_ascii=False, sort_keys=True, default=str,
    )  # fmt: skip
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def lease_seconds(env: Any = None) -> float:
    raw = (os.environ if env is None else env).get(LEASE_ENV)
    try:
        return max(1.0, float(raw)) if raw else DEFAULT_LEASE
    except ValueError:
        return DEFAULT_LEASE


class Inflight:
    """계산 중 표시와 기다리던 쪽에 줄 답. SQLite(프로세스끼리) 또는 메모리(테스트)."""

    def __init__(self, path: str | Path | None = None, lease: float | None = None,
                 poll: float = POLL_SECONDS) -> None:  # fmt: skip
        self.lease = lease if lease is not None else lease_seconds()
        self.poll = poll
        self._lock = threading.Lock()
        self._marks: dict[str, tuple[str, float]] = {}
        self._answers: dict[str, tuple[str, float, str]] = {}
        self._conn: sqlite3.Connection | None = None
        if path is not None:
            if str(path) != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5.0,
                                         isolation_level=None)  # fmt: skip
            with self._lock:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("CREATE TABLE IF NOT EXISTS llm_inflight (key TEXT PRIMARY KEY,"
                                   " owner TEXT NOT NULL, until REAL NOT NULL)")  # fmt: skip
                self._conn.execute("CREATE TABLE IF NOT EXISTS llm_answers (key TEXT PRIMARY KEY,"
                                   " owner TEXT NOT NULL, at REAL NOT NULL,"
                                   " value TEXT NOT NULL)")  # fmt: skip

    def try_claim(self, key: str, now: float | None = None) -> tuple[str | None, str | None]:
        """(내 주인 이름, 지금 주인 이름). 다른 쪽의 살아 있는 표시가 있으면 (None, 그 주인).
        만료된 표시는 넘겨받는다."""
        now = time.time() if now is None else now
        owner = f"{os.getpid()}:{uuid.uuid4().hex[:12]}"
        with self._lock:
            if self._conn is None:
                mark = self._marks.get(key)
                if mark is not None and mark[1] >= now:
                    return None, mark[0]
                self._marks[key] = (owner, now + self.lease)
                return owner, owner
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT owner, until FROM llm_inflight WHERE key = ?", (key,)
                ).fetchone()
                if row is not None and row[1] >= now:
                    self._conn.execute("COMMIT")
                    return None, row[0]
                self._conn.execute("INSERT OR REPLACE INTO llm_inflight VALUES (?, ?, ?)",
                                   (key, owner, now + self.lease))  # fmt: skip
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            return owner, owner

    def claim(self, key: str, now: float | None = None) -> str | None:
        """표시를 남기고 내 주인 이름. 다른 쪽의 살아 있는 표시가 있으면 None."""
        return self.try_claim(key, now)[0]

    def release(self, key: str, owner: str, value: Any = None) -> None:
        """표시를 지운다. 성공했으면(`value`) 기다리던 쪽이 읽을 답을 같이 남긴다 (한 번에)."""
        now = time.time()
        body = None if value is None else json.dumps(value, ensure_ascii=False, default=str)
        with self._lock:
            if self._conn is None:
                if body is not None:
                    self._answers[key] = (owner, now, body)
                if self._marks.get(key, ("",))[0] == owner:
                    del self._marks[key]
                for k in [k for k, (_, at, _) in self._answers.items() if at < now - KEEP_SECONDS]:
                    del self._answers[k]
                return
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                if body is not None:
                    self._conn.execute("INSERT OR REPLACE INTO llm_answers VALUES (?, ?, ?, ?)",
                                       (key, owner, now, body))  # fmt: skip
                self._conn.execute("DELETE FROM llm_inflight WHERE key = ? AND owner = ?",
                                   (key, owner))  # fmt: skip
                self._conn.execute("DELETE FROM llm_answers WHERE at < ?", (now - KEEP_SECONDS,))
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def answer_of(self, key: str, owners: Collection[str]) -> Any | None:
        """`owners`(기다리면서 본 주인) 중 하나가 남긴 답. 없으면 None."""
        if not owners:
            return None
        with self._lock:
            if self._conn is None:
                got = self._answers.get(key)
                body = got[2] if got is not None and got[0] in owners else None
            else:
                row = self._conn.execute(
                    "SELECT owner, value FROM llm_answers WHERE key = ?", (key,)
                ).fetchone()
                body = row[1] if row is not None and row[0] in owners else None
        return json.loads(body) if body is not None else None

    async def run[T](
        self,
        key: str,
        call: Callable[[], Awaitable[T]],
        dump: Callable[[T], Any],
        load: Callable[[Any], T],
    ) -> T:
        """같은 요청이 계산 중이면 기다렸다 그 답, 아니면 표시를 남기고 `call`."""
        seen: set[str] = set()  # 기다리면서 본 주인
        while True:
            mine, holder = self.try_claim(key)
            if mine is not None:
                # 답 확인 직후 주인이 끝나 표시가 비었을 수 있다 (답 남기기·표시 지우기는 한 번에
                # 하므로 표시를 남긴 뒤에는 그 답이 보인다) → 다시 부르지 않음
                if (value := self.answer_of(key, seen)) is not None:
                    self.release(key, mine)
                    return load(value)
                try:
                    result = await call()
                except BaseException:
                    self.release(key, mine)  # 답 없이 지움 → 기다리던 쪽이 대신 부른다
                    raise
                self.release(key, mine, dump(result))
                return result
            if holder is not None:
                seen.add(holder)
            await asyncio.sleep(self.poll)
            if (value := self.answer_of(key, seen)) is not None:
                return load(value)


INFLIGHT = Inflight(os.environ.get(DB_ENV) or None)
