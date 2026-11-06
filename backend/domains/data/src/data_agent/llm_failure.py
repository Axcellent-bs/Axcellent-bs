"""이 계산 중 LLM 호출이 실패했는지 (크레딧·연결·형식 오류). 실패한 결과는 저장하지 않는다.

- data LLM 래퍼(`handlers.TimedLLMClient`)가 호출이 예외로 끝나면 지금 범위(`scope`)에 표시한다
- 결과를 저장하는 곳(화면 계산 저장 `_stored`·브리프·기획안 초안·영상 탐색)은 `scope()` 안에서
  계산하고, 표시가 있으면 결과는 돌려주되 저장하지 않는다. 다음 요청이 다시 시도한다
- 분류·형식·설명 문장 저장분은 이미 unknown·고정 문장을 저장하지 않는다 (항목마다 거른다)
- 범위는 asyncio 작업 사이에도 같은 표시를 나눈다 (ContextVar에 같은 객체)
- LLM 하루 상한에 닿아 덜 만든 계산은 실패와 따로 `capped`로 표시한다. 그 결과(브리프·기회 카드·
  예시 아이디어)는 하루 캐시가 아니라 짧게만(`cap_ttl`, 기본 1시간) 저장해 상한이 풀리면 다시 만든다
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta

CAP_TTL_ENV = "DATA_LLM_CAP_CACHE_MINUTES"
DEFAULT_CAP_TTL_MINUTES = 60


@dataclass
class Scope:
    failed: bool = False
    reason: str | None = None
    capped: bool = False  # LLM 하루 상한에 닿아 덜 만든 계산 (실패와 따로)


_CURRENT: ContextVar[Scope | None] = ContextVar("data_llm_failure", default=None)


@contextmanager
def scope() -> Iterator[Scope]:
    """새 범위. 바깥 범위가 있으면 안에서 난 실패를 바깥에도 알린다."""
    outer = _CURRENT.get()
    mine = Scope()
    token = _CURRENT.set(mine)
    try:
        yield mine
    finally:
        _CURRENT.reset(token)
        if outer is not None and mine.failed:
            outer.failed, outer.reason = True, outer.reason or mine.reason
        if outer is not None and mine.capped:
            outer.capped = True


def mark(error: BaseException) -> None:
    """지금 범위에 실패를 표시한다 (범위 밖이면 아무것도 안 함).

    LLM 설정이 없는 것(`LLMNotConfiguredError`)은 실패로 보지 않는다: 다시 시도해도 결과가 같으니
    그 대체 결과(고정 문장·unknown)를 저장해도 된다. 하루 상한(`LLMDailyCapReached`)은 실패가
    아니라 `capped`로 표시한다 (상한이 풀리면 결과가 달라지므로 짧게만 저장).
    """
    from ax_agent_template.llm import LLMNotConfiguredError
    from data_agent.llm_budget import LLMDailyCapReached
    from data_agent.llm_halt import LLMHalted

    current = _CURRENT.get()
    # 오류 원인으로 멈춘 것(키 오류·공급자 한도)도 풀리면 결과가 달라지므로 상한과 같이 짧게만 저장
    if isinstance(error, LLMDailyCapReached | LLMHalted):
        if current is not None:
            current.capped = True
        return
    if isinstance(error, LLMNotConfiguredError):
        return
    if current is not None and not current.failed:
        current.failed, current.reason = True, type(error).__name__


def failed() -> bool:
    current = _CURRENT.get()
    return bool(current and current.failed)


def capped() -> bool:
    """지금 범위에서 LLM 하루 상한 때문에 부르지 못한 호출이 있었는지."""
    current = _CURRENT.get()
    return bool(current and current.capped)


def cap_ttl(env: Mapping[str, str] | None = None) -> timedelta:
    """상한으로 덜 만든 결과를 저장해 둘 시간 (`DATA_LLM_CAP_CACHE_MINUTES`, 기본 60분)."""
    raw = str((os.environ if env is None else env).get(CAP_TTL_ENV, "")).strip()
    try:
        minutes = int(raw)
    except ValueError:
        minutes = DEFAULT_CAP_TTL_MINUTES
    return timedelta(minutes=minutes if minutes > 0 else DEFAULT_CAP_TTL_MINUTES)
