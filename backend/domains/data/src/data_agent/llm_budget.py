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
- skill·작업별로도 센다 (`by_task`: 문장·분류·계획·프로필·예시 아이디어·임베딩 등)
- 남겨 둘 몫: 사용량이 상한의 `DATA_LLM_RESERVE_RATIO`(기본 0.8)에 닿으면 브리프(기회 카드·수집
  계획 포함, `PRIORITY_SKILLS`)만 부르고 나머지(분류 등)는 `LLMDailyCapReached`로 다음 날로 미룬다
"""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

from ax_agent_template.llm import LLMNotConfiguredError

# 한 작업(브리프 한 판 등)이 쓴 LLM 토큰을 세는 칸. `counting()` 안에서 부른 LLM 요청(같은 asyncio
# 문맥)의 토큰을 더한다. 다른 곳에서 같은 요청을 계산 중이라 기다려 받은 답(#835)은 0이다
_SINK: ContextVar[list[int] | None] = ContextVar("data_llm_token_sink", default=None)


@contextmanager
def counting() -> Iterator[list[int]]:
    """`with counting() as box:` 동안 쓴 토큰 합이 `box[0]`."""
    box = [0]
    token = _SINK.set(box)
    try:
        yield box
    finally:
        _SINK.reset(token)


def count_tokens(tokens: int) -> None:
    box = _SINK.get()
    if box is not None:
        box[0] += tokens


DB_ENV = "DATA_LLM_BUDGET_DB"
CALLS_ENV = "DATA_LLM_DAILY_CALLS"
TOKENS_ENV = "DATA_LLM_DAILY_TOKENS"
DEFAULT_CALLS = 200
DEFAULT_TOKENS = 400_000
RESERVE_ENV = "DATA_LLM_RESERVE_RATIO"
DEFAULT_RESERVE = 0.8
# 남겨 둔 몫을 쓸 skill: 브리프 (기회 카드·수집 계획·프로필·주제 분류는 브리프 안에서 부른다),
# 직접 대화(data_chat, 대화 하루 상한 30회·80,000토큰 안에서만 — `data_chat.chat_limit_reason`)
PRIORITY_SKILLS = frozenset({"trend_insight_brief", "data_chat"})
KST = timezone(timedelta(hours=9))


# 답 형식이 정의된 모듈 → 작업 이름 (사용량 표). 모르는 모듈은 모듈 이름 그대로
TASKS = {
    "explain": "문장(설명)",
    "trend_insight_brief": "문장(브리프 한 줄)",
    "domain_opportunity": "문장(기회 카드 한 줄)",
    "trend_evidence": "문장(왜 뜨나)",
    "root_cause": "문장(원인)",
    "topic_category": "분류(주제·영상)",
    "shorts_stats": "분류(숏츠 형식)",
    "meme_format": "분류(밈 형식)",
    "domain_subtopics": "분류(하위 주제)",
    "source_plan": "계획",
    "domain_profile": "프로필",
    "promo_concepts": "예시 아이디어",
}
EMBED_TASK = "임베딩"


def task_of(schema: object) -> str:
    """LLM 답 형식(스키마 클래스) → 작업 이름."""
    module = str(getattr(schema, "__module__", "") or "").rsplit(".", 1)[-1]
    return TASKS.get(module, module or "unknown")


def reserve_ratio(env: Mapping[str, str] | None = None) -> float:
    """이 비율부터 브리프 몫만 (`DATA_LLM_RESERVE_RATIO`, 0 초과 1 이하, 아니면 0.8)."""
    raw = str((os.environ if env is None else env).get(RESERVE_ENV, "")).strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_RESERVE
    return value if 0 < value <= 1 else DEFAULT_RESERVE


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

    def share(self) -> float:
        """상한 대비 사용 비율 (호출·토큰 중 큰 쪽)."""
        return max(self.calls / self.call_cap, self.tokens / self.token_cap)

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
                self._conn.execute(
                    "CREATE TABLE IF NOT EXISTS usage_by (day TEXT NOT NULL, skill TEXT NOT NULL,"
                    " task TEXT NOT NULL, calls INTEGER NOT NULL, tokens INTEGER NOT NULL,"
                    " PRIMARY KEY (day, skill, task))"
                )
        self._mem_by: dict[tuple[str, str, str], tuple[int, int]] = {}

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

    def check(self, env: Mapping[str, str] | None = None, skill: str | None = None) -> None:
        """상한에 닿았으면 `LLMDailyCapReached` (LLM을 부르기 전에). 남겨 둔 몫 비율에 닿았으면
        브리프(`PRIORITY_SKILLS`)가 아닌 skill도 다음 날로 미룬다."""
        now = self.usage(env)
        if now.reached:
            raise LLMDailyCapReached(f"LLM 하루 상한에 닿아 ({now.line()})")
        ratio = reserve_ratio(env)
        if skill not in PRIORITY_SKILLS and now.share() >= ratio:
            raise LLMDailyCapReached(
                f"LLM 하루 상한의 {ratio:.0%}에 닿아 브리프·기회 카드 몫만 남기고 다음 날로 미룸"
                f" ({now.line()})"
            )

    def reserving(self, env: Mapping[str, str] | None = None) -> bool:
        now = self.usage(env)
        return not now.reached and now.share() >= reserve_ratio(env)

    def add(self, calls: int, tokens: int, day: date | None = None, *,
            skill: str | None = None, task: str | None = None) -> None:  # fmt: skip
        key = (day or self.today()).isoformat()
        by = (key, skill or "unknown", task or "unknown")
        with self._lock:
            if self._conn is not None:
                with self._conn:
                    self._conn.execute(
                        "INSERT INTO usage VALUES (?, ?, ?) ON CONFLICT(day) DO UPDATE SET"
                        " calls = calls + excluded.calls, tokens = tokens + excluded.tokens",
                        (key, calls, tokens),
                    )
                    self._conn.execute(
                        "INSERT INTO usage_by VALUES (?, ?, ?, ?, ?) ON CONFLICT(day, skill, task)"
                        " DO UPDATE SET calls = calls + excluded.calls,"
                        " tokens = tokens + excluded.tokens",
                        (*by, calls, tokens),
                    )
                    old = (date.fromisoformat(key) - timedelta(days=30)).isoformat()
                    self._conn.execute("DELETE FROM usage WHERE day < ?", (old,))
                    self._conn.execute("DELETE FROM usage_by WHERE day < ?", (old,))
                return
            old = self._mem.get(key, (0, 0))
            self._mem[key] = (old[0] + calls, old[1] + tokens)
            was = self._mem_by.get(by, (0, 0))
            self._mem_by[by] = (was[0] + calls, was[1] + tokens)

    def by_task(self, day: date | None = None) -> list[tuple[str, str, int, int]]:
        """(skill, 작업, 호출, 토큰) 토큰 많은 순."""
        key = (day or self.today()).isoformat()
        with self._lock:
            if self._conn is not None:
                rows = self._conn.execute(
                    "SELECT skill, task, calls, tokens FROM usage_by WHERE day = ?", (key,)
                ).fetchall()
            else:
                rows = [(s, t, c, n) for (d, s, t), (c, n) in self._mem_by.items() if d == key]
        return sorted(((s, t, int(c), int(n)) for s, t, c, n in rows),
                      key=lambda r: (-r[3], -r[2], r[0], r[1]))  # fmt: skip


BUDGET = Budget(os.environ.get(DB_ENV) or None)
