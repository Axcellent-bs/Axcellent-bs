"""이 계산 중 LLM 호출이 실패했는지 (크레딧·연결·형식 오류). 실패한 결과는 저장하지 않는다.

- data LLM 래퍼(`handlers.TimedLLMClient`)가 호출이 예외로 끝나면 지금 범위(`scope`)에 표시한다
- 결과를 저장하는 곳(화면 계산 저장 `_stored`·브리프·기획안 초안·영상 탐색)은 `scope()` 안에서
  계산하고, 표시가 있으면 결과는 돌려주되 저장하지 않는다. 다음 요청이 다시 시도한다
- 분류·형식·설명 문장 저장분은 이미 unknown·고정 문장을 저장하지 않는다 (항목마다 거른다)
- 범위는 asyncio 작업 사이에도 같은 표시를 나눈다 (ContextVar에 같은 객체)
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class Scope:
    failed: bool = False
    reason: str | None = None


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


def mark(error: BaseException) -> None:
    """지금 범위에 실패를 표시한다 (범위 밖이면 아무것도 안 함).

    LLM 설정이 없거나 하루 상한에 닿은 것(`LLMNotConfiguredError`)은 실패로 보지 않는다:
    다시 시도해도 같은 날엔 결과가 같으니 그 대체 결과(고정 문장·unknown)를 저장해도 된다.
    """
    from ax_agent_template.llm import LLMNotConfiguredError

    if isinstance(error, LLMNotConfiguredError):
        return
    current = _CURRENT.get()
    if current is not None and not current.failed:
        current.failed, current.reason = True, type(error).__name__


def failed() -> bool:
    current = _CURRENT.get()
    return bool(current and current.failed)
