"""요청 단계별 시간 기록 (트렌드 감지 탭 속도 확인용).

요청 하나가 sqlite 조회·외부 호출(YouTube·네이버)·LLM에 쓴 시간을 ms로 모아 한 줄로 남긴다.
계산 = 전체 − 셋. 값·키·입력은 남기지 않고 단계 이름과 시간, 캐시 결과(hit/miss/stored)만 남긴다.

- 모으는 범위: `timed_handler`로 감싼 skill이 도는 동안 (ContextVar라 동시 요청끼리 섞이지 않는다.
  같은 요청 안에서 동시에 부른 호출은 각각 더한다 → 대기 합이 전체보다 클 수 있다)
- sqlite: `install_sqlite_timer()`가 `sqlite3.connect`의 기본 연결 클래스를 시간 재는 하위 클래스로
  바꾼다(execute·fetch 시간). 이 서버 프로세스와 수집기에서만 켠다
- 외부·LLM: 커넥터의 HTTP 지점과 LLM 클라이언트가 `stage("external")`·`stage("llm")`로 잰다
- 출력: uvicorn 로그(`uvicorn.error` 로거, INFO). 수집기는 표준 출력
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

STAGES = ("sqlite", "external", "llm")
_CURRENT: ContextVar[Timing | None] = ContextVar("data_stage_timing", default=None)
_log = logging.getLogger("uvicorn.error")


class Timing:
    def __init__(self) -> None:
        self.seconds: dict[str, float] = dict.fromkeys(STAGES, 0.0)
        self.cache: str | None = None
        self.started = time.perf_counter()
        self.name: str | None = None  # 기록 이름 (YouTube 장부의 부른 곳)

    def line(self, name: str) -> str:
        total = time.perf_counter() - self.started
        waits = sum(self.seconds.values())
        parts = [f"skill={name}", f"total_ms={total * 1000:.0f}"]
        parts += [f"{k}_ms={v * 1000:.0f}" for k, v in self.seconds.items()]
        parts.append(f"compute_ms={max(0.0, total - waits) * 1000:.0f}")
        if self.cache:
            parts.append(f"cache={self.cache}")
        return "[timing] " + " ".join(parts)


@contextmanager
def stage(name: str) -> Iterator[None]:
    """`name` 단계에 걸린 시간을 지금 요청 기록에 더한다 (기록 중이 아니면 아무것도 안 한다)."""
    current = _CURRENT.get()
    if current is None:
        yield
        return
    t = time.perf_counter()
    try:
        yield
    finally:
        current.seconds[name] = current.seconds.get(name, 0.0) + time.perf_counter() - t


def note_cache(result: str) -> None:
    """캐시 결과 (hit, miss, stored). 키는 남기지 않는다."""
    current = _CURRENT.get()
    if current is not None:
        current.cache = result


def current_name() -> str | None:
    """지금 단계 기록 중인 이름 (skill 이름, 미리 계산이면 `skill@precompute1`). 없으면 None."""
    current = _CURRENT.get()
    return current.name if current is not None else None


@contextmanager
def recording(name: str, emit: Callable[[str], None] | None = None) -> Iterator[Timing]:
    """이 블록 동안 단계 시간을 모으고 끝나면 한 줄 남긴다."""
    timing = Timing()
    timing.name = name
    token = _CURRENT.set(timing)
    try:
        yield timing
    finally:
        _CURRENT.reset(token)
        (emit or _log.info)(timing.line(name))


def timed_handler[R](
    name: str, handler: Callable[[Any], Awaitable[R]]
) -> Callable[[Any], Awaitable[R]]:
    async def run(request: Any) -> R:
        with recording(name):
            return await handler(request)

    run.__name__ = getattr(handler, "__name__", name)
    run.__doc__ = handler.__doc__
    return run


class _TimedCursor(sqlite3.Cursor):
    def execute(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        with stage("sqlite"):
            return super().execute(*args, **kwargs)

    def executemany(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        with stage("sqlite"):
            return super().executemany(*args, **kwargs)

    def fetchone(self) -> Any:
        with stage("sqlite"):
            return super().fetchone()

    def fetchmany(self, *args: Any, **kwargs: Any) -> list[Any]:
        with stage("sqlite"):
            return super().fetchmany(*args, **kwargs)

    def fetchall(self) -> list[Any]:
        with stage("sqlite"):
            return super().fetchall()

    def __next__(self) -> Any:
        with stage("sqlite"):
            return super().__next__()


class TimedConnection(sqlite3.Connection):
    """execute·fetch 시간을 지금 요청의 sqlite 단계에 더하는 연결."""

    def cursor(self, factory: Any = _TimedCursor) -> Any:  # noqa: ANN401
        return super().cursor(factory)

    def execute(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        return self.cursor().execute(*args, **kwargs)

    def executemany(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        return self.cursor().executemany(*args, **kwargs)


_original_connect = sqlite3.connect


def _connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
    kwargs.setdefault("factory", TimedConnection)
    return _original_connect(*args, **kwargs)


def install_sqlite_timer() -> None:
    """이 프로세스의 `sqlite3.connect` 기본 연결을 시간 재는 연결로 (여러 번 불러도 한 번)."""
    sqlite3.connect = _connect
