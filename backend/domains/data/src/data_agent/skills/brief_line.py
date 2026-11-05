"""브리프 인사이트·기회 카드 한 줄(line)의 검사와 짧은 고정 문장. 코드 규칙만.

- LLM 한 줄은 숫자 검증·단정 금지에 더해 **80자 이하·숫자 3개 이하**여야 쓴다
  (연령대의 "20대"도 숫자 하나로 센다)
- 통과하지 못하면 facts를 나열하지 않고 **근거 1개만 쓴 짧은 고정 문장(40자 이내)** 하나.
  근거는 평소 대비 배수 중 가장 큰 것, 배수가 없으면 첫 숫자
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from data_agent.explain import _bad_numbers
from data_agent.skills.trend_evidence import BLAME

MAX_CHARS = 80
MAX_NUMBERS = 3
FIXED_MAX = 40
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")

# LLM 프롬프트에 넣는 길이 규칙 (인사이트·기회 카드 공통)
LENGTH_RULE = (
    f"- 한 문장, {MAX_CHARS}자 이내. 숫자는 {MAX_NUMBERS}개까지만"
    " (연령대의 '20대'도 숫자 하나로 센다). 가장 센 근거 1~2개만 고르고 나열하지 말 것\n"
)


def count_numbers(text: str) -> int:
    return len(_NUMBER.findall(text))


def problem(line: str, facts: Mapping[str, int | float | str]) -> str | None:
    """한 줄을 쓸 수 없는 이유 (쓸 수 있으면 None)."""
    text = line.strip()
    if not text:
        return "LLM 답에 이 항목 한 줄이 없음"
    if any(b in text for b in BLAME):
        return "원인 단정 말이 있음"
    if _bad_numbers(text, dict(facts)):
        return "근거에 없는 숫자가 있음"
    if len(text) > MAX_CHARS:
        return f"{MAX_CHARS}자를 넘음 ({len(text)}자)"
    if count_numbers(text) > MAX_NUMBERS:
        return f"숫자가 {MAX_NUMBERS}개를 넘음 ({count_numbers(text)}개)"
    return None


def _num(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 100 else f"{round(v, 2):g}"


def _label(key: str) -> str:
    for word in (" 평소 대비 배수", " 평소 대비", " 배수", " (기간 평균 대비)"):
        key = key.replace(word, "")
    return " ".join(key.replace("·", " ").split())


def short_fixed(facts: Mapping[str, int | float | str]) -> str:
    """근거 1개만 쓴 고정 문장 (40자 이내, 숫자는 facts 그대로)."""
    numeric = {k: float(v) for k, v in facts.items()
               if isinstance(v, int | float) and not isinstance(v, bool)}  # fmt: skip
    ratios = {k: v for k, v in numeric.items() if "배" in k}
    if ratios:
        key = max(ratios, key=lambda k: ratios[k])
        label = _label(key)
        if "평소" in key:
            tail = f": 평소의 {_num(ratios[key])}배예요"
        else:  # 시즌 배율·전체 대비 등: 기준 말을 그대로
            tail = f": {_num(ratios[key])}배예요"
    elif numeric:
        key = next(iter(numeric))
        label, tail = _label(key), f": {_num(numeric[key])}"
    else:
        return "근거 숫자가 없어요"
    room = FIXED_MAX - len(tail)
    if len(label) > room:
        label = label[: max(1, room - 1)].rstrip() + "…"
    return f"{label}{tail}"
