"""계산된 숫자(facts)에 대한 한두 문장 설명 생성.

- 숫자는 코드가 계산해서 넘기고, LLM은 문장만 쓴다.
- LLM 문장 속 숫자가 facts에 없으면 버리고 고정 템플릿으로 대체한다(fallback=True).
- LLM 예외(미설정·타임아웃 등)와 스키마 검증 실패도 템플릿으로 대체한다.
- 대체할 때 이유를 남긴다(`Explanation.reason`). LLM 문장 원문은 남기지 않는다.
  - `llm_unavailable`: 모델 설정이 없음
  - `llm_error`: 호출 실패 (HTTP 오류·시간 초과 등)
  - `format_error`: 응답이 형식(스키마)에 맞지 않거나 문장이 비어 있음
  - `number_invalid`: 문장에 계산에 없는 숫자가 있음. 걸린 숫자는 `bad_numbers`
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from ax_agent_template.llm import LLMClient, LLMFormatError, LLMNotConfiguredError

# 1,234 / 1234 / 0.5 / 12.5 형태. 퍼센트 기호는 숫자 뒤에 붙으므로 숫자만 뽑으면 된다.
_NUMBER_RE = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")

_PROMPT = (
    "아래 kind의 분석 결과를 한국어 한두 문장으로 설명해라.\n"
    "규칙: facts에 있는 숫자만 쓰고 숫자를 바꾸거나 새로 만들지 말 것. "
    "원인을 단정하지 말 것. 판단·추측·권고는 쓰지 말 것.\n"
    "kind: {kind}\nfacts: {facts}"
)


class _Summary(BaseModel):
    summary: str


LLM_UNAVAILABLE = "llm_unavailable"
LLM_ERROR = "llm_error"
FORMAT_ERROR = "format_error"
NUMBER_INVALID = "number_invalid"
FALLBACK_REASONS = (LLM_UNAVAILABLE, LLM_ERROR, FORMAT_ERROR, NUMBER_INVALID)


@dataclass(frozen=True)
class Explanation:
    summary: str
    fallback: bool
    reason: str | None = None  # 템플릿으로 대체한 이유 (`FALLBACK_REASONS`), 대체 안 했으면 None
    bad_numbers: tuple[float, ...] = ()  # `number_invalid`일 때 계산에 없던 숫자

    def note(self) -> str | None:
        """근거(evidence)에 붙일 한 줄. 대체하지 않았으면 None. LLM 문장 원문은 넣지 않는다."""
        if not self.fallback:
            return None
        line = f"설명 문장을 템플릿으로 대체함: {self.reason or 'unknown'}"
        if self.bad_numbers:
            line += " (계산에 없는 숫자: " + ", ".join(_show(n) for n in self.bad_numbers) + ")"
        return line


def _show(number: float) -> str:
    """숫자 표기 (정수면 소수점 없이, 지수 표기 없이)."""
    return str(int(number)) if number.is_integer() else repr(number)


def _extract_numbers(text: str) -> set[float]:
    """문장에서 숫자(쉼표·소수 포함)를 모두 뽑는다."""
    return {float(m.replace(",", "")) for m in _NUMBER_RE.findall(text)}


def _fact_numbers(facts: dict[str, int | float | str]) -> set[float]:
    """facts에서 허용 숫자 집합을 만든다. 문자열 값 안의 숫자도 포함한다."""
    allowed: set[float] = set()
    for value in facts.values():
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            allowed.add(float(value))
        else:
            allowed |= _extract_numbers(str(value))
    return allowed


def _bad_numbers(summary: str, facts: dict[str, int | float | str]) -> list[float]:
    """문장 속 숫자 중 facts에 없는 것 (작은 것부터)."""
    allowed = _fact_numbers(facts)
    return sorted(
        n for n in _extract_numbers(summary) if not any(abs(n - a) < 1e-9 for a in allowed)
    )


def _numbers_valid(summary: str, facts: dict[str, int | float | str]) -> bool:
    return not _bad_numbers(summary, facts)


def _fmt(value: object, *, comma: bool = False) -> str:
    """값을 문자열로. 키가 없으면 '-'."""
    if value is None:
        return "-"
    if comma and isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"
    return str(value)


def _kpi_template(facts: dict[str, int | float | str]) -> str:
    """1차·2차 사건 MTTR 비교 한 줄. facts의 diff_minutes는 1차 − 2차(분)다."""
    diff = facts.get("diff_minutes")
    if not isinstance(diff, int | float) or isinstance(diff, bool):
        return "1차·2차 사건의 복구 시간을 비교할 수 없습니다."
    if diff == 0:
        return "2차 사건은 1차와 같은 시간에 복구했습니다."
    if diff < 0:
        return f"2차 사건은 1차보다 {_fmt(-diff)}분 늦게 복구했습니다."
    memory = "회사 기억을 참조해 " if facts.get("second_used_memory") == "예" else ""
    return f"2차 사건은 {memory}1차보다 {_fmt(diff)}분 빨리 복구했습니다."


def _template(kind: str, facts: dict[str, int | float | str]) -> str:
    if kind == "impact":
        return (
            f"장애 구간 실패 주문 {_fmt(facts.get('failed_order_count'))}건, "
            f"영향 고객 {_fmt(facts.get('affected_customer_count'))}명, "
            f"피해 금액 {_fmt(facts.get('damage_amount'), comma=True)}원입니다."
        )
    if kind == "anomaly":
        return (
            f"{_fmt(facts.get('metric'))} 지표가 기준선 {_fmt(facts.get('baseline'))} 대비 "
            f"{_fmt(facts.get('observed'))}로 {_fmt(facts.get('ratio'))}배입니다."
        )
    if kind == "kpi":
        return _kpi_template(facts)
    pairs = ", ".join(f"{k}={v}" for k, v in facts.items())
    return f"{kind}: {pairs}" if pairs else f"{kind}: 표시할 값이 없습니다."


async def explain(kind: str, facts: dict[str, int | float | str], llm: LLMClient) -> Explanation:
    """facts를 설명하는 문장을 만든다. 검증에 실패하면 템플릿(fallback=True)과 이유를 쓴다."""

    def fallback(reason: str, numbers: list[float] | None = None) -> Explanation:
        return Explanation(_template(kind, facts), True, reason, tuple(numbers or ()))

    try:
        result = await llm.structured(_PROMPT.format(kind=kind, facts=facts), _Summary)
        summary = result.summary.strip()
    except LLMNotConfiguredError:
        return fallback(LLM_UNAVAILABLE)
    except (LLMFormatError, ValidationError):
        return fallback(FORMAT_ERROR)
    except Exception:  # 호출 실패(HTTP 오류·시간 초과 등) 모두 템플릿으로 대체
        return fallback(LLM_ERROR)
    if not summary:
        return fallback(FORMAT_ERROR)
    if bad := _bad_numbers(summary, facts):
        return fallback(NUMBER_INVALID, bad)
    return Explanation(summary, False)
