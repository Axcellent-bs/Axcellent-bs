"""계산된 숫자(facts)에 대한 한두 문장 설명 생성.

- 숫자는 코드가 계산해서 넘기고, LLM은 문장만 쓴다.
- LLM 문장 속 숫자가 facts 값에서 나온 표기(그대로·반올림·퍼센트·쉼표·단위)가 아니면 버리고
  고정 템플릿으로 대체한다(fallback=True). 규칙은 `_token_valid`.
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
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, ValidationError

from ax_agent_template.llm import LLMClient, LLMFormatError, LLMNotConfiguredError
from data_agent import catalog

# 1,234 / 1234 / 0.5 / 12.5 형태. 숫자 뒤의 퍼센트 기호와 이름·단위 꼬리("xx", "분")도 같이 본다.
_NUMBER_RE = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
_TOKEN_RE = re.compile(r"(?P<num>\d+(?:,\d{3})*(?:\.\d+)?)(?P<pct>\s*%)?(?P<tail>[xX]{2}|\s*분)?")

_PROMPT = (
    "아래 kind의 분석 결과를 한국어 한두 문장으로 설명해라.\n"
    "규칙: facts에 있는 숫자만 쓰고 숫자를 바꾸거나 새로 만들지 말 것. "
    "원인을 단정하지 말 것. 판단·추측·권고는 쓰지 말 것.\n"
    "kind: {kind}\nfacts: {facts}"
)
# kind별로 더 붙이는 규칙. 여기 없는 kind는 위 문장 그대로다(평가 프롬프트가 바뀌지 않게).
_KIND_RULES = {
    "activity": "\n추가 규칙: 한두 문장 대신 항목마다 한 줄씩, 최대 3줄로 쓸 것. "
    "줄은 줄바꿈으로 나눌 것. 항목 이름은 facts의 한국어 이름 그대로 쓸 것.",
}


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


def _decimals(text: str) -> int:
    _, _, frac = text.partition(".")
    return len(frac)


def _rounded(value: float, places: int) -> set[Decimal]:
    """facts 값 하나를 소수 `places` 자리로 반올림한 결과 (사사오입·오사오입·2진 표기 모두)."""
    shown = Decimal(repr(value))
    if _decimals(str(shown)) <= places:
        return {shown}  # 자릿수를 줄이지 않았으면 원래 값 그대로만
    step = Decimal(1).scaleb(-places)
    return {
        shown.quantize(step, rounding=ROUND_HALF_UP),
        shown.quantize(step, rounding=ROUND_HALF_EVEN),
        Decimal(value).quantize(step, rounding=ROUND_HALF_EVEN),
    }


def _same_value(written: Decimal, places: int, fact: float) -> bool:
    """문장 속 숫자가 facts 값 하나와 같은 값인지 (그대로이거나, 자릿수만 줄인 반올림)."""
    if Decimal(repr(fact)) == written:
        return True
    if written == 0:  # 0이 아닌 값을 반올림해 0이 된 것은 같은 값으로 보지 않는다
        return False
    return written in _rounded(fact, places)


def _code_names(facts: dict[str, int | float | str]) -> set[str]:
    """facts 문자열 값에 있는 코드 이름 (예: "5xx")."""
    names: set[str] = set()
    for value in facts.values():
        if isinstance(value, str):
            names |= {m.lower() for m in re.findall(r"\d+xx", value, flags=re.IGNORECASE)}
    return names


def _minute_facts(facts: dict[str, int | float | str]) -> set[float]:
    """구간 길이(분)로 쓸 수 있는 facts 숫자 (키에 minute·window가 들어간 값)."""
    return {
        float(v)
        for k, v in facts.items()
        if isinstance(v, int | float)
        and not isinstance(v, bool)
        and ("minute" in k or "window" in k)
    }


def _token_valid(
    match: re.Match[str], facts: dict[str, int | float | str], allowed: set[float]
) -> bool:
    """문장 속 숫자 하나가 facts에서 나온 표기인지.

    - 그대로·천 단위 쉼표·단위(원·건·명) 붙은 숫자: facts 값과 같으면 통과
    - 반올림: 소수 자릿수를 줄인 값이 facts 값의 반올림과 정확히 같을 때만 (2.747 → 2.75, 2.7, 3)
    - 퍼센트: "42%"는 facts 값 0.42(비율)나 42와 같은 값일 때 (반올림 규칙 같음)
    - 이름·단위의 일부: "5xx"는 facts의 코드 목록에, "5분"은 facts의 구간 길이에 그대로 있을 때만
    facts 값끼리 계산한 값(합·차·비율)은 후보에 없으므로 통과하지 않는다.
    """
    text = match.group("num").replace(",", "")
    written, places = Decimal(text), _decimals(text)
    tail = (match.group("tail") or "").strip().lower()
    if tail == "xx":
        return f"{text}xx" in _code_names(facts)
    if tail == "분":
        return any(Decimal(repr(m)) == written for m in _minute_facts(facts))
    if match.group("pct"):
        return any(
            _same_value(written, places, a)
            or _same_value(written, places, float(Decimal(repr(a)) * 100))
            for a in allowed
        )
    return any(_same_value(written, places, a) for a in allowed)


def _bad_numbers(summary: str, facts: dict[str, int | float | str]) -> list[float]:
    """문장 속 숫자 중 facts 값에서 나온 표기가 아닌 것 (작은 것부터)."""
    allowed = _fact_numbers(facts)
    return sorted(
        {
            float(m.group("num").replace(",", ""))
            for m in _TOKEN_RE.finditer(summary)
            if not _token_valid(m, facts, allowed)
        }
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


def _activity_template(facts: dict[str, int | float | str]) -> str:
    """특이사항 한 줄씩 (activity_report). facts 키: 항목N·직전N·이번N·변화율N·방향N."""
    lines = []
    i = 1
    while f"항목{i}" in facts:
        lines.append(
            f"{facts[f'항목{i}']}: 직전 {_fmt(facts.get(f'직전{i}'), comma=True)} → "
            f"이번 {_fmt(facts.get(f'이번{i}'), comma=True)} "
            f"({_fmt(facts.get(f'변화율{i}'))}% {_fmt(facts.get(f'방향{i}'))})"
        )
        i += 1
    return "\n".join(lines) or "특이사항 없음"


def _trend_template(facts: dict[str, int | float | str]) -> str:
    """검색어 급상승 한 줄 (trend_scan). facts 키: 급상승 수·그룹 수·판정 불가 수·키워드N·배수N."""
    parts = []
    i = 1
    while f"키워드{i}" in facts:
        parts.append(f"{facts[f'키워드{i}']}({_fmt(facts.get(f'배수{i}'))}배)")
        i += 1
    head = f"키워드 그룹 {_fmt(facts.get('그룹 수'))}개 중 급상승 {_fmt(facts.get('급상승 수'))}개"
    unknown = facts.get("판정 불가 수")
    tail = f", 판정 불가 {_fmt(unknown)}개" if unknown else ""
    return f"{head}{tail}" + (f": {', '.join(parts)}" if parts else "") + "."


def _campaign_template(facts: dict[str, int | float | str]) -> str:
    """캠페인 효과 한두 문장 (campaign_effect). 확인할 수 없는 항목은 그 이유를 그대로 쓴다."""
    if "주문 방향" in facts:
        excess = facts.get("주문 증분")
        sign = "+" if isinstance(excess, int | float) and excess > 0 else ""
        head = (
            f"캠페인 기간 주문 {_fmt(facts.get('캠페인 주문'), comma=True)}건으로, 지난 "
            f"{_fmt(facts.get('기준 일수'))}일 같은 시간대 평균 "
            f"{_fmt(facts.get('평소 주문'))}건 대비 "
        )
        direction = str(facts.get("주문 방향") or "")
        # 같음인데 증분이 0이 아님: "증가 추정 +N건, 아직 통계적으로 확실하지 않음"
        if "추정" in direction:
            word, _, rest = direction.partition(" 추정")
            orders = f"{head}{word} 추정 {sign}{_fmt(excess)}건{rest}입니다."
        else:
            orders = f"{head}{sign}{_fmt(excess)}건({_fmt(facts.get('주문 방향'))})입니다."
    else:
        orders = f"주문은 {_fmt(facts.get('주문'))}."
    if "쿠폰 사용률" in facts:
        rate = facts.get("쿠폰 사용률")
        shown = f"{round(rate * 100, 1)}%" if isinstance(rate, int | float) else "-"
        coupons = (
            f" 캠페인 쿠폰 {_fmt(facts.get('쿠폰 발급'), comma=True)}장 중 "
            f"{_fmt(facts.get('쿠폰 사용'), comma=True)}장 사용({shown}, 조회 시점 누적)."
        )
    else:
        coupons = f" 쿠폰은 {_fmt(facts.get('쿠폰'))}."
    return orders + coupons


def _template(kind: str, facts: dict[str, int | float | str]) -> str:
    if kind == "campaign":
        return _campaign_template(facts)
    if kind == "activity":
        return _activity_template(facts)
    if kind == "trend":
        return _trend_template(facts)
    if kind == "trend_group":
        return (
            f"{_fmt(facts.get('키워드'))} 검색이 직전 {_fmt(facts.get('기준선 일수'))}일 평소의 "
            f"{_fmt(facts.get('배수'))}배입니다(z {_fmt(facts.get('z'))})."
        )
    if kind == "impact":
        words = catalog.current().impact  # 회사 카탈로그의 단위 (캣북스: 주문·고객·원)
        return (
            f"장애 구간 실패 {words.unit_label} {_fmt(facts.get('failed_order_count'))}건, "
            f"영향 {words.customer_label} {_fmt(facts.get('affected_customer_count'))}명, "
            f"피해 금액 {_fmt(facts.get('damage_amount'), comma=True)}{words.money_unit}입니다."
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
        prompt = _PROMPT.format(kind=kind, facts=facts) + _KIND_RULES.get(kind, "")
        result = await llm.structured(prompt, _Summary)
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


# ---------------------------------------------------------- 영상 소재 아이디어 (content_recommend)

IDEA_FORMATS = ("숏폼", "롱폼")
_IDEA_PROMPT = (
    "트렌드 소재 하나로 회사가 만들 영상 아이디어를 한국어 한두 문장으로 제안하고, "
    "형식을 숏폼과 롱폼 중 하나로 골라라.\n"
    "규칙: numbers에 있는 숫자만 쓰고 숫자를 바꾸거나 새로 만들지 말 것. "
    "소재 이름은 데이터 블록의 이름만 쓸 것. 출연자·사건·날짜 같은 사실을 지어내지 말 것.\n"
    "numbers: {numbers}"
)


class _Idea(BaseModel):
    idea: str
    format: Literal["숏폼", "롱폼"]


@dataclass(frozen=True)
class Idea:
    idea: str
    format: str  # IDEA_FORMATS 중 하나
    fallback: bool
    reason: str | None = None
    bad_numbers: tuple[float, ...] = ()

    def note(self) -> str | None:
        return Explanation(self.idea, self.fallback, self.reason, self.bad_numbers).note()


def _idea_template(numbers: dict[str, int | float | str], names: dict[str, str]) -> str:
    keyword = names.get("키워드", "")
    head = f"{keyword} 검색이 평소의 {_fmt(numbers.get('배수'))}배로 올랐습니다"
    videos = numbers.get("YouTube 영상 수")
    if videos is not None:
        head += f"(YouTube 인기 영상 {_fmt(videos)}개)"
    return head + ". 이 소재를 짧은 영상으로 다뤄 볼 만합니다."


async def explain_idea(
    numbers: dict[str, int | float | str],
    names: dict[str, str],
    llm: LLMClient,
    context_id: str | None = None,
) -> Idea:
    """소재 하나의 영상 아이디어 문장과 형식.

    숫자는 `numbers`만 프롬프트에 넣고, 이름(외부에서 온 태그 등)은 `names`로 데이터 블록에만
    넣는다. 숫자 검증은 `explain`과 같은 규칙(`_bad_numbers`)이고 이름 속 숫자(예: t1)도
    허용한다. 실패하면 고정 문장 + 숏폼, `fallback=True`와 이유.
    """
    facts: dict[str, int | float | str] = {**numbers, **names}

    def fallback(reason: str, bad: list[float] | None = None) -> Idea:
        return Idea(_idea_template(numbers, names), "숏폼", True, reason, tuple(bad or ()))

    try:
        prompt = _IDEA_PROMPT.format(numbers=numbers)
        result = await llm.structured(prompt, _Idea, untrusted=dict(names), context_id=context_id)
        idea = result.idea.strip()
    except LLMNotConfiguredError:
        return fallback(LLM_UNAVAILABLE)
    except (LLMFormatError, ValidationError):
        return fallback(FORMAT_ERROR)
    except Exception:  # 호출 실패(HTTP 오류·시간 초과 등) 모두 고정 문장으로 대체
        return fallback(LLM_ERROR)
    if not idea or result.format not in IDEA_FORMATS:
        return fallback(FORMAT_ERROR)
    if bad := _bad_numbers(idea, facts):
        return fallback(NUMBER_INVALID, bad)
    return Idea(idea, result.format, False)
