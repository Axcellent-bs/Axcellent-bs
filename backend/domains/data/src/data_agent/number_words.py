"""의뢰 숫자(나이대·길이)를 말로 바꾸고, 결론 검사에서 그 의뢰의 숫자만 허용하는 순수 함수 (11/23).

분석 보고서 추천(`skills/trend_report.recommendation_section`)은 결론 한 줄에 숫자가 있으면 버린다
(`trend_memes.check_line`, 숫자는 코드 값만). 그런데 의뢰_타깃 "남성 20대·30대"·
의뢰_형식 "30초 이내"를 숫자로 넘기면 LLM이 그대로 따라 써서 결론이 통째로 버려졌다(TR-0A49).

- 프롬프트 데이터: 나이대(10~60대) → "이십대", 길이(1~180초) → "삼십 초"
- 결론 검사: 그 의뢰 brief에서 나온 나이대·초 표현(20대·20·30대·30초)만 지운 뒤 숫자를 본다.
  조회수·배수·% 같은 그 밖의 숫자는 지금처럼 버린다
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

_DIGITS = ("", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구")
AGE_MIN, AGE_MAX = 10, 60
SECONDS_MIN, SECONDS_MAX = 1, 180
_SECONDS = re.compile(r"(?<!\d)(\d{1,3})\s*초")
_SEP = r"\s*[·,/~∼\-]\s*"


def sino(n: int) -> str:
    """1~999를 한자어 수 말로: 20 → 이십, 105 → 백오, 180 → 백팔십."""
    if not 1 <= n <= 999:
        raise ValueError(f"1~999만 바꾼다: {n}")
    hundreds, rest = divmod(n, 100)
    tens, ones = divmod(rest, 10)
    out = ""
    if hundreds:
        out += ("" if hundreds == 1 else _DIGITS[hundreds]) + "백"
    if tens:
        out += ("" if tens == 1 else _DIGITS[tens]) + "십"
    return out + _DIGITS[ones]


def age_word(age: str | int) -> str:
    """나이대 하나: "20" → "이십대". 10~60대(10 단위)가 아니면 숫자 그대로 "<n>대"."""
    try:
        n = int(age)
    except (TypeError, ValueError):
        return f"{age}대"
    if AGE_MIN <= n <= AGE_MAX and n % 10 == 0:
        return f"{sino(n)}대"
    return f"{n}대"


def ages_words(ages: Sequence[str | int]) -> str:
    """["20", "30"] → "이십대·삼십대"."""
    return "·".join(age_word(a) for a in ages)


def target_words(gender_word: str | None, ages: Sequence[str | int]) -> str:
    """의뢰_타깃 말: "남성 이십대·삼십대". 아무것도 없으면 "전체"."""
    parts = [gender_word] if gender_word else []
    if ages:
        parts.append(ages_words(ages))
    return " ".join(parts) or "전체"


def seconds_in(text: str | None) -> list[int]:
    """글 안의 "<n>초" 숫자들(1~180만)."""
    return [n for n in (int(m) for m in _SECONDS.findall(text or ""))
            if SECONDS_MIN <= n <= SECONDS_MAX]  # fmt: skip


def seconds_to_words(text: str | None) -> str:
    """"30초 이내 쇼츠" → "삼십 초 이내 쇼츠". 1~180초만 바꾸고 그 밖은 그대로."""

    def swap(m: re.Match[str]) -> str:
        n = int(m.group(1))
        return f"{sino(n)} 초" if SECONDS_MIN <= n <= SECONDS_MAX else m.group(0)

    return _SECONDS.sub(swap, text or "")


def _alt(values: Iterable[int | str]) -> str:
    uniq = sorted({str(int(v)) for v in values}, key=lambda s: (-len(s), s))
    return "|".join(re.escape(v) for v in uniq)


def strip_brief_numbers(text: str, ages: Sequence[str | int], seconds: Sequence[int]) -> str:
    """그 의뢰의 나이대·초 표현만 지운다. "20·30대"·"20대·30대"·"20, 30대"·"30초".

    다른 숫자(조회수·배수·%, 의뢰에 없는 나이대·초)는 그대로 남아 검사에서 걸린다."""
    out = text
    good_ages = [a for a in ages if str(a).isdigit()]
    if good_ages:
        one = rf"(?<!\d)(?:{_alt(good_ages)})(?!\d)"
        out = re.sub(rf"{one}(?:\s*대)?(?:{_SEP}{one}(?:\s*대)?)*\s*대", " ", out)
    if seconds:
        out = re.sub(rf"(?<!\d)(?:{_alt(seconds)})(?!\d)\s*초", " ", out)
    return out


__all__ = [
    "age_word",
    "ages_words",
    "seconds_in",
    "seconds_to_words",
    "sino",
    "strip_brief_numbers",
    "target_words",
]
