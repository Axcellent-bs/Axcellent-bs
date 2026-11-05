"""data 부서 LLM 하루 상한 (호출 수·토큰 수). 닿으면 LLM을 부르지 않는다.

- 상한: `DATA_LLM_DAILY_CALLS`(기본 200회), `DATA_LLM_DAILY_TOKENS`(기본 400,000 토큰, 입력 + 출력).
  숫자가 아니거나 0 이하면 기본값
- 셈: LLM 래퍼가 HTTP 요청 한 번마다 남기는 사용량(`record_usage`, 재시도·실패 포함)을 한국 날짜로
  센다. 기록은 SQLite(`DATA_LLM_BUDGET_DB`, run_all.sh가 `backend/.data/data_llm_budget.db`로 준다,
  서버·수집기가 같은 파일). 환경변수가 없으면 프로세스 메모리(테스트)
- 닿으면 `LLMDailyCapReached`(= `LLMNotConfiguredError`)를 올린다. 부르는 쪽은 LLM 설정이 없을 때와
  같은 길(고정 문장·unknown)로 가고, 이유는 `no_llm(error)`로 "LLM 하루 상한에 닿아 …"가 된다.
  저장된 캐시(분류·설명 문장·브리프)는 그대로 쓴다
- 출처 현황(`data_sources`)에 오늘 사용량/상한을 보여 준다
"""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

from ax_agent_template.llm import LLMNotConfiguredError

DB_ENV = "DATA_LLM_BUDGET_DB"
CALLS_ENV = "DATA_LLM_DAILY_CALLS"
TOKENS_ENV = "DATA_LLM_DAILY_TOKENS"
DEFAULT_CALLS = 200
DEFAULT_TOKENS = 400_000
KST = timezone(timedelta(hours=9))


class LLMDailyCapReached(LLMNotConfiguredError):
    """오늘 data LLM 상한에 닿음. 설정 없음과 같은 대체 길로 간다 (이유 문장만 다르다)."""


def no_llm(error: Exception) -> str:
    """대체 이유의 앞머리: 상한이면 "LLM 하루 상한에 닿아 (…)", 아니면 "LLM 설정이 없어"."""
    return str(error) if isinstance(error, LLMDailyCapReached) else "LLM 설정이 없어"


def _cap(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        value = int(str(env.get(name, "")).strip())
    except ValueError:
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class Usage:
    day: date
    calls: int
    tokens: int
    call_cap: int
    token_cap: int

    @property
    def reached(self) -> bool:
        return self.calls >= self.call_cap or self.tokens >= self.token_cap

    def line(self) -> str:
        return (f"오늘 호출 {self.calls}/{self.call_cap}회, "
                f"토큰 {self.tokens:,}/{self.token_cap:,}")  # fmt: skip


class Budget:
    def __init__(self, path: str | Path | None = None) -> None:
        self._lock = threading.Lock()
        self._mem: dict[str, tuple[int, int]] = {}
        self._conn: sqlite3.Connection | None = None
        if path is not None:
            if str(path) != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5.0)
            with self._lock, self._conn:
                self._conn.execute(
                    "CREATE TABLE IF NOT EXISTS usage (day TEXT PRIMARY KEY,"
                    " calls INTEGER NOT NULL, tokens INTEGER NOT NULL)"
                )

    @staticmethod
    def today() -> date:
        return datetime.now(UTC).astimezone(KST).date()

    def usage(self, env: Mapping[str, str] | None = None, day: date | None = None) -> Usage:
        env = os.environ if env is None else env
        key = (day or self.today()).isoformat()
        with self._lock:
            if self._conn is not None:
                row = self._conn.execute(
                    "SELECT calls, tokens FROM usage WHERE day = ?", (key,)
                ).fetchone()
                calls, tokens = (int(row[0]), int(row[1])) if row else (0, 0)
            else:
                calls, tokens = self._mem.get(key, (0, 0))
        caps = _cap(env, CALLS_ENV, DEFAULT_CALLS), _cap(env, TOKENS_ENV, DEFAULT_TOKENS)
        return Usage(date.fromisoformat(key), calls, tokens, *caps)

    def check(self, env: Mapping[str, str] | None = None) -> None:
        """상한에 닿았으면 `LLMDailyCapReached` (LLM을 부르기 전에)."""
        now = self.usage(env)
        if now.reached:
            raise LLMDailyCapReached(f"LLM 하루 상한에 닿아 ({now.line()})")

    def add(self, calls: int, tokens: int, day: date | None = None) -> None:
        key = (day or self.today()).isoformat()
        with self._lock:
            if self._conn is not None:
                with self._conn:
                    self._conn.execute(
                        "INSERT INTO usage VALUES (?, ?, ?) ON CONFLICT(day) DO UPDATE SET"
                        " calls = calls + excluded.calls, tokens = tokens + excluded.tokens",
                        (key, calls, tokens),
                    )
                    old = (date.fromisoformat(key) - timedelta(days=30)).isoformat()
                    self._conn.execute("DELETE FROM usage WHERE day < ?", (old,))
                return
            old = self._mem.get(key, (0, 0))
            self._mem[key] = (old[0] + calls, old[1] + tokens)


BUDGET = Budget(os.environ.get(DB_ENV) or None)
