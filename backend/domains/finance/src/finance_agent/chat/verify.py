"""재무 대화 답의 숫자 확인: 답에 나온 금액·건수·비율이 코드가 모은 facts(와 사람이 한 말)에 있는지.

프롬프트로 "facts에 없는 숫자는 말하지 않는다"고 하지만 LLM이 지어내거나 바꿔 쓸 수 있다
(반올림, 합산, 다른 줄 숫자). 재무 숫자는 틀리면 안 되므로 코드로 한 번 더 본다.

- 보는 숫자: 단위(원·건·명·곳·%·만·억)가 붙었거나 100 이상인 숫자. 단위 없는 작은 숫자
  (순서, "1~2분")는 보지 않는다
- 허용: facts 안의 모든 숫자(문자열 속 날짜·금액 포함)와 그 절댓값, 비율 소수(0.031)의 백분율(3.1%),
  사람이 대화에서 쓴 숫자. "16만 원"·"1.2억 원"은 원 단위로 바꿔 맞춘다(반올림한 값이면 걸린다)
- 날짜·시각("2026-11-09", "10월 22일", "14:30")은 facts의 날짜 조각과 맞춘다
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

# 숫자 + (만|억)? + 단위?  — 쉼표 묶음(1,234,567)과 소수(3.1)를 함께 읽는다
_NUM = re.compile(
    r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*(조|억|만)?\s*(원|건|명|곳|개|%|퍼센트)?"
)
_DATE_PARTS = re.compile(r"\d+")
_SCALE = {"만": Decimal(10_000), "억": Decimal(100_000_000), "조": Decimal(10**12)}
SMALL = 100  # 단위가 없으면 이보다 작은 숫자는 보지 않는다


def _num(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def allowed_numbers(*sources: Any) -> set[Decimal]:
    """facts·대화에 있는 숫자 전부(정수·소수·문자열 속 숫자, 절댓값, 비율의 백분율)."""
    out: set[Decimal] = set()

    def add(v: Decimal) -> None:
        out.add(v)
        out.add(abs(v))
        if Decimal(0) < abs(v) <= 1:  # 비율 0.031 → 3.1%
            out.add((abs(v) * 100).normalize())

    def walk(x: Any) -> None:
        if isinstance(x, bool) or x is None:
            return
        if isinstance(x, int | float):
            add(Decimal(str(x)))
        elif isinstance(x, str):
            for m in _NUM.finditer(x):
                base = _num(m.group(1) + (m.group(2) or ""))
                if base is not None:
                    add(base)
                    if m.group(3):
                        add(base * _SCALE[m.group(3)])
            for part in _DATE_PARTS.findall(x):  # 날짜·시각 조각
                add(Decimal(int(part)))
        elif isinstance(x, dict):
            for k, v in x.items():
                walk(k)
                walk(v)
        elif isinstance(x, list | tuple | set):
            for v in x:
                walk(v)

    for s in sources:
        walk(s)
    return out


def bad_numbers(reply: str, allowed: set[Decimal]) -> list[str]:
    """답에서 facts·대화에 없는 숫자(쓴 그대로의 글자). 없으면 빈 목록."""
    bad: list[str] = []
    for m in _NUM.finditer(reply):
        whole, frac, scale, unit = m.group(1), m.group(2) or "", m.group(3), m.group(4)
        value = _num(whole + frac)
        if value is None:
            continue
        if not scale and not unit and value < SMALL and "," not in whole:
            continue
        if scale:
            value = value * _SCALE[scale]
        candidates = {value, value.normalize()}
        if unit in ("%", "퍼센트"):
            candidates.add((value / 100).normalize())
        if not any(c in allowed for c in candidates):
            bad.append(m.group(0).strip())
    return list(dict.fromkeys(bad))
