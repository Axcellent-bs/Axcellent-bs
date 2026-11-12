"""판매자 정산 받기: 고객지원(support, settlement 서비스)이 계산한 정산을 A2A로 읽는다.

판매자 정산(정산금 계산 · 지급 승인 요청 · 묶음 상태 · 지급 기한 점검)은 고객지원이 한다
(2026-11-12 결정). 재무는 정산금을 계산하지 않고 지급 승인도 요청하지 않는다.
여기서 받은 결과를 화면에 그대로 보여 주고, 회계(판매자 미지급금 롤포워드 · 13주 현금 흐름 ·
은행 출금 대조 · 마감 점검 · 재무 대화)에만 쓴다.

| 함수 | 고객지원 skill | 돌려주는 것 |
| --- | --- | --- |
| `settlement(period)` | `seller_settlement` | 정산 내역서(`meta` 포함) |
| `weeks(periods)` | `seller_settlement_weeks` | 기간 → 내역서(`meta` 뺀 것) |
| `batches()` | `seller_settlement_batches` | 기간 → 묶음 상태 |
| `deadlines()` | `seller_settlement_deadlines` | 지급 기한 점검(`meta` 포함) |

- 못 받으면(고객지원이 꺼짐·실패·형식이 다름) None이다. 숫자를 지어내거나 0으로 채우지 않는다
- 고객지원이 이 skill을 맡은 서비스를 아직 등록하지 않았으면 묻지 않고 None이다
  (그대로 부르면 꺼진 서비스로 보고 사람 할 일함에 넘기기 때문이다. 인사 `team_headcount`와 같다)
- 잘못된 기간이라고 답하면(`invalid_input`) `InvalidPeriod`를 낸다(화면은 400)
- 화면이 몇 초마다 불러도 같은 요청을 반복하지 않게 잠깐 기억한다(실패는 더 짧게)
- 같은 요청이 이미 가 있으면 새로 보내지 않고 그 답을 같이 기다린다(skill·입력마다 하나만).
  고객지원 정산이 느릴 때 화면·마감 점검·할 일이 같은 요청을 겹쳐 쌓지 않게 한다
- 고객지원이 첫 장부를 모으는 중(`settlement_preparing`)이면 None이고 `preparing()`이 True다
  (화면은 503 "준비 중")
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from ax_agent_template import call_agent
from ax_agent_template.services import route_target
from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus

SUPPORT = "support"
CALL_SECONDS = 10.0
CACHE_SECONDS = 30.0
FAILED_SECONDS = 10.0
PREPARING = "settlement_preparing"  # 고객지원이 첫 장부를 모으는 중
MAX_WEEKS = 30  # 고객지원 `seller_settlement_weeks`가 한 번에 받는 기간 수

Caller = Callable[[str, dict[str, Any]], Awaitable[AgentResult | None]]


class InvalidPeriod(ValueError):
    """고객지원이 기간 형식이 틀렸다고 답했다."""


async def _call_support(skill: str, payload: dict[str, Any]) -> AgentResult | None:
    """고객지원 skill을 A2A로 부른다. 맡은 서비스가 없으면 부르지 않고 None."""
    if route_target(SUPPORT, skill) == SUPPORT:
        return None
    meta = RequestMeta(
        context_id=f"ctx_finance_{skill}",
        token="finance-dashboard",
        deadline=datetime.now(UTC) + timedelta(seconds=CALL_SECONDS),
    )
    return await call_agent(SUPPORT, AgentRequest(meta=meta, skill=skill, input=payload))


_caller: Caller = _call_support
_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
_inflight: dict[str, asyncio.Task[AgentResult | None]] = {}  # 가 있는 요청 (열쇠마다 하나)
_preparing: dict[str, bool] = {}  # skill → 마지막 답이 "준비 중"이었는가


def set_caller(caller: Caller | None) -> None:
    """고객지원을 부르는 함수를 바꾼다(테스트용). None이면 원래대로. 기억한 값도 버린다."""
    global _caller
    _caller = caller or _call_support
    forget()


def forget() -> None:
    """기억해 둔 응답을 모두 버린다."""
    _cache.clear()
    _inflight.clear()
    _preparing.clear()


def preparing(skill: str = "seller_settlement") -> bool:
    """그 skill의 마지막 답이 "첫 장부를 모으는 중"이었는가 (None을 503 준비 중으로 보여 줄 때)."""
    return _preparing.get(skill, False)


async def _call_once(key: str, skill: str, payload: dict[str, Any]) -> AgentResult | None:
    """같은 열쇠의 요청이 가 있으면 그 답을 같이 기다린다."""
    task = _inflight.get(key)
    if task is None or task.done():
        task = asyncio.ensure_future(_caller(skill, payload))
        _inflight[key] = task
        task.add_done_callback(
            lambda t: _inflight.pop(key, None) if _inflight.get(key) is t else None
        )
    return await asyncio.shield(task)


async def _ask(
    skill: str, payload: dict[str, Any], *, strict: bool = False
) -> dict[str, Any] | None:
    """고객지원 응답 output. 못 받으면 None. `strict`면 `invalid_input`을 `InvalidPeriod`로 낸다."""
    key = f"{skill}:{json.dumps(payload, sort_keys=True)}"
    now = time.monotonic()
    hit = _cache.get(key)
    if hit is not None and now - hit[0] < (CACHE_SECONDS if hit[1] is not None else FAILED_SECONDS):
        return hit[1]
    try:
        result = await _call_once(key, skill, payload)
    except Exception:  # noqa: BLE001 - 고객지원이 꺼져 있어도 재무 화면은 뜬다
        result = None
    output: dict[str, Any] | None = None
    if result is not None and result.status == TaskStatus.COMPLETED:
        output = result.output if isinstance(result.output, dict) else None
    elif strict and result is not None and result.outcome == "invalid_input":
        raise InvalidPeriod(f"고객지원이 기간을 받지 않았다: {payload}")
    _preparing[skill] = output is None and result is not None and result.outcome == PREPARING
    _cache[key] = (now, output)
    return output


def _dict_of_dicts(value: Any) -> dict[str, dict[str, Any]] | None:
    if not isinstance(value, dict):
        return None
    return {str(k): v for k, v in value.items() if isinstance(v, dict)}


async def settlement(period: str | None) -> dict[str, Any] | None:
    """`seller_settlement`: 그 주(None이면 지난주) 정산 내역서. `meta`가 들어 있다."""
    out = await _ask("seller_settlement", {"period": period}, strict=True)
    if out is None or not isinstance(out.get("statements"), list) or "period" not in out:
        return None
    return out


async def weeks(periods: list[str]) -> dict[str, dict[str, Any]] | None:
    """`seller_settlement_weeks`: 기간 → 내역서. 하나라도 빠지면 None(일부만 쓰지 않는다)."""
    wanted = sorted(set(periods))
    if not wanted:
        return {}
    found: dict[str, dict[str, Any]] = {}
    for k in range(0, len(wanted), MAX_WEEKS):
        chunk = wanted[k : k + MAX_WEEKS]
        out = await _ask("seller_settlement_weeks", {"periods": chunk})
        rows = _dict_of_dicts((out or {}).get("weeks"))
        if rows is None or any(
            not isinstance((rows.get(p) or {}).get("statements"), list) for p in chunk
        ):
            return None
        found |= {p: rows[p] for p in chunk}
    return found


async def batches() -> dict[str, dict[str, Any]] | None:
    """`seller_settlement_batches`: 기간 → 묶음 상태(안전 장치에서 다시 읽어 반영한 것)."""
    out = await _ask("seller_settlement_batches", {})
    return _dict_of_dicts((out or {}).get("batches")) if out is not None else None


async def deadlines() -> dict[str, Any] | None:
    """`seller_settlement_deadlines`: 판매 대금 지급 기한 점검. `meta`가 들어 있다."""
    out = await _ask("seller_settlement_deadlines", {})
    if out is None or not isinstance(out.get("sellers"), list):
        return None
    return out


def needs_attention(report: dict[str, Any]) -> list[dict[str, Any]]:
    """사람이 볼 판매자: 기한 지남·위험·임박 (고객지원이 매긴 상태 그대로)."""
    return [r for r in report.get("sellers") or [] if r.get("status") != "ok"]
