"""의뢰 입력 채우기: 고객 문장에서 부서 skill의 입력 칸을 규칙으로 만든다 (의뢰 자동 넘김 18번).

부서 skill은 기간·월·시간 구간 같은 구조화된 입력을 요구한다. 고객은 "이번 달", "어제", "지난주"처럼
말하므로 그 말을 부서가 받는 날짜 모양으로 바꿔 준다.

- LLM은 쓰지 않는다. 정해진 규칙(정규식)만 쓰고 결과는 부서가 받는 값의 모양뿐이다. 고객 문장은
  지시가 아니라 데이터로만 읽는다(문장에서 읽는 것은 기간 말과 숫자뿐이다).
- 못 채우면(기간 말이 없거나 부서가 지원하지 않는 기간이면) 지어내지 않고 `ask`에 고객에게 물을
  정해진 문장을 돌려준다. 이때 부서에는 보내지 않는다.
- 기간 말이 아예 없을 때 부서가 기본값(예: 매출 최근 7일)을 갖는 일은 입력을 비워 보낸다.
- 날짜는 서울 시간 기준이다.
- 일 목록(`request_tasks.py`)의 단계 `fill`에 이 모듈의 규칙 이름을 적는다.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

KST = timezone(timedelta(hours=9))
MAX_SEGMENT_TEXT = 200  # 고객층 질문(`segment_question.text`) 길이 한도
MAX_QUERY = 500
MAX_WINDOW_HOURS = 168


@dataclass(frozen=True)
class Fill:
    """입력 채우기 결과. `ask`가 있으면 못 채운 것이다(부서에 보내지 않고 고객에게 묻는다)."""

    payload: dict[str, Any] = field(default_factory=dict)
    ask: str | None = None
    note: str | None = None  # 어떻게 읽었는지 (고객 안내에 붙인다)


def _today(now: datetime) -> date:
    aware = now if now.tzinfo else now.replace(tzinfo=KST)
    return aware.astimezone(KST).date()


def _has(text: str, pattern: str) -> bool:
    return re.search(pattern, text) is not None


def _month_shift(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _valid(month: int) -> bool:
    return 1 <= month <= 12


def _month_of(text: str, today: date, *, upcoming: bool) -> tuple[int, int] | None:
    """말에서 (연, 월)을 읽는다. 없으면 None. `upcoming`이면 지난 달 이름은 내년으로 본다."""
    for pattern in (r"(\d{4})\s*년\s*(\d{1,2})\s*월", r"(?<!\d)(\d{4})-(\d{1,2})(?!\d)"):
        found = re.search(pattern, text)
        if found and _valid(int(found.group(2))):
            return int(found.group(1)), int(found.group(2))
    if _has(text, r"다음\s*달|내달|익월"):
        return _month_shift(today.year, today.month, 1)
    if _has(text, r"지난\s*달|전월|저번\s*달"):
        return _month_shift(today.year, today.month, -1)
    if _has(text, r"이번\s*달|이달|금월"):
        return today.year, today.month
    found = re.search(r"(?<!\d)(\d{1,2})\s*월", text)
    if found and _valid(int(found.group(1))):
        month = int(found.group(1))
        year = today.year + (1 if upcoming and month < today.month else 0)
        return year, month
    return None


def _iso_week(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


# ---- 규칙 ----------------------------------------------------------------------------------

SALES_ASK = "매출은 오늘, 최근 7일, 최근 30일 중에서 볼 수 있어요. 어느 기간으로 볼까요?"
EVENT_ASK = "몇 월 이벤트를 제안해 드릴까요? 예: '12월', '다음 달'"
HEADCOUNT_ASK = "몇 월의 팀별 인원을 알려 드릴까요? 예: '이번 달', '12월'"
SETTLEMENT_ASK = "판매자 정산은 주 단위로 볼 수 있어요. 어느 주로 볼까요? 예: '이번 주', '지난주'"


def sales_period(text: str, now: datetime, request_id: str | None = None) -> Fill:
    """매출 분석: 오늘·최근 7일·최근 30일만 지원한다. 기간 말이 없으면 부서 기본값(7일)."""
    unsupported = (
        r"어제|그제|그저께|지난\s*달|지난달|작년|올해|분기|지난\s*주|지난주|(?<!\d)\d{1,2}\s*월"
    )
    if _has(text, unsupported):
        return Fill(ask=SALES_ASK)
    if _has(text, r"오늘|금일"):
        return Fill({"period": "today"}, note="오늘 기준")
    if _has(text, r"이번\s*달|이번달|한\s*달|30\s*일|월간|월별"):
        return Fill({"period": "30d"}, note="최근 30일 기준")
    if _has(text, r"이번\s*주|이번주|일주일|7\s*일|주간|한\s*주"):
        return Fill({"period": "7d"}, note="최근 7일 기준")
    return Fill()


def closing_period(text: str, now: datetime, request_id: str | None = None) -> Fill:
    """손익 정리: 일·주·월 단위와 그 기준 날짜. 말이 없으면 오늘 하루."""
    today = _today(now)
    if _has(text, r"지난\s*달|지난달|전월|저번\s*달"):
        year, month = _month_shift(today.year, today.month, -1)
        return Fill(
            {"period": "month", "date": date(year, month, 1).isoformat()},
            note=f"{year}년 {month}월 기준",
        )
    if _has(text, r"지난\s*주|지난주|전주"):
        day = today - timedelta(days=7)
        return Fill({"period": "week", "date": day.isoformat()}, note="지난주 기준")
    found = re.search(r"(?<!\d)(\d{1,2})\s*월", text)
    if found and _valid(int(found.group(1))):  # 지난 달을 가리킨다: 이번 달보다 뒤면 작년
        month = int(found.group(1))
        year = today.year if month <= today.month else today.year - 1
        return Fill(
            {"period": "month", "date": date(year, month, 1).isoformat()},
            note=f"{year}년 {month}월 기준",
        )
    if _has(text, r"이번\s*달|이번달|이달|금월|월간|월별"):
        return Fill({"period": "month", "date": today.isoformat()}, note="이번 달 기준")
    if _has(text, r"이번\s*주|이번주|금주|주간|주별"):
        return Fill({"period": "week", "date": today.isoformat()}, note="이번 주 기준")
    if _has(text, r"어제"):
        day = today - timedelta(days=1)
        return Fill({"period": "day", "date": day.isoformat()}, note="어제 기준")
    return Fill({"period": "day", "date": today.isoformat()}, note="오늘 기준")


def _month_rule(ask: str, *, upcoming: bool) -> Callable[..., Fill]:
    def rule(text: str, now: datetime, request_id: str | None = None) -> Fill:
        found = _month_of(text, _today(now), upcoming=upcoming)
        if found is None:
            return Fill(ask=ask)
        year, month = found
        return Fill({"month": f"{year}-{month:02d}"}, note=f"{year}년 {month}월 기준")

    return rule


def settlement_week(text: str, now: datetime, request_id: str | None = None) -> Fill:
    """판매자 정산: ISO 주(`YYYY-Www`). 주가 없으면 부서 기본 주. 달 단위 말은 지원하지 않는다."""
    today = _today(now)
    if _has(text, r"지난\s*달|지난달|이번\s*달|이번달|월간|월별|(?<!\d)\d{1,2}\s*월"):
        return Fill(ask=SETTLEMENT_ASK)
    if _has(text, r"지난\s*주|지난주|전주"):
        return Fill({"period": _iso_week(today - timedelta(days=7))}, note="지난주 기준")
    if _has(text, r"이번\s*주|이번주|금주"):
        return Fill({"period": _iso_week(today)}, note="이번 주 기준")
    return Fill()


def anomaly_window(text: str, now: datetime, request_id: str | None = None) -> Fill:
    """지표 이상 점검: 시간대가 있는 시각 구간. 말이 없으면 최근 24시간."""
    end = now if now.tzinfo else now.replace(tzinfo=KST)
    end = end.astimezone(KST).replace(microsecond=0)
    midnight = datetime.combine(end.date(), datetime.min.time(), KST)
    hours = re.search(r"(\d{1,3})\s*시간", text)
    if _has(text, r"어제"):
        start, stop, note = midnight - timedelta(days=1), midnight, "어제 하루 기준"
    elif hours and int(hours.group(1)) > 0:
        count = min(int(hours.group(1)), MAX_WINDOW_HOURS)
        start, stop, note = end - timedelta(hours=count), end, f"최근 {count}시간 기준"
    elif _has(text, r"오늘|금일") and end > midnight:
        start, stop, note = midnight, end, "오늘 기준"
    elif _has(text, r"이번\s*주|이번주|일주일|7\s*일"):
        start, stop, note = end - timedelta(days=7), end, "최근 7일 기준"
    else:
        start, stop, note = end - timedelta(hours=24), end, "최근 24시간 기준"
    return Fill({"window_from": start.isoformat(), "window_to": stop.isoformat()}, note=note)


def customer_text(text: str, now: datetime, request_id: str | None = None) -> Fill:
    """고객 질문 그대로(고객층 질문 `text`). 문장은 데이터로만 쓰이며 길이만 자른다."""
    return Fill({"text": text.strip()[:MAX_SEGMENT_TEXT]})


def search_query(text: str, now: datetime, request_id: str | None = None) -> Fill:
    """과거 기록 찾기 `query`: 고객 문장 그대로(길이만 자른다)."""
    return Fill({"query": text.strip()[:MAX_QUERY]})


NOTICE_ASK = "어떤 공지인가요? 장애 안내, 복구 안내, 신상품 안내 중에서 알려 주세요."
MAX_TICKET_TITLE = 100
MAX_TICKET_EVIDENCE = 400


def notice_kind(text: str, now: datetime, request_id: str | None = None) -> Fill:
    """공지 초안: 문장의 키워드로 공지 종류를 정한다. 시각 같은 세부 값은 비워 보낸다(고정 문안)."""
    if _has(text, r"복구|정상화|복귀|해결|재개"):
        kind, note = "incident_resolved", "복구 안내 공지로 작성"
    elif _has(text, r"신상품|신간|신규\s*상품|새\s*상품|상품\s*등록|입고"):
        kind, note = "product_created", "신상품 안내 공지로 작성"
    elif _has(text, r"점검|장애|오류|지연|접속|결제\s*불가|중단|먹통"):
        kind, note = "incident_opened", "장애 안내 공지로 작성"
    else:
        return Fill(ask=NOTICE_ASK)
    return Fill({"kind": kind, "facts": {}}, note=note)


def ticket_text(text: str, now: datetime, request_id: str | None = None) -> Fill:
    """기능 개발 요청: 문장을 제목으로, 근거는 의뢰 번호와 문장 앞부분으로 채운다(길이는 자른다)."""
    one = re.sub(r"\s+", " ", text).strip()
    tag = f" {request_id}" if request_id else ""
    payload: dict[str, Any] = {
        "title": one[:MAX_TICKET_TITLE],
        "evidence": f"고객 의뢰{tag}: {one[:MAX_TICKET_EVIDENCE]}",
    }
    if request_id:
        payload["source_ref"] = request_id
    return Fill(payload)


RULES: dict[str, Callable[..., Fill]] = {
    "notice_kind": notice_kind,
    "ticket_text": ticket_text,
    "sales_period": sales_period,
    "closing_period": closing_period,
    "month_upcoming": _month_rule(EVENT_ASK, upcoming=True),
    "month_any": _month_rule(HEADCOUNT_ASK, upcoming=False),
    "settlement_week": settlement_week,
    "anomaly_window": anomaly_window,
    "customer_text": customer_text,
    "search_query": search_query,
}


def fill(rule: str, text: str, now: datetime, request_id: str | None = None) -> Fill:
    """규칙 이름으로 입력을 만든다. 모르는 규칙 이름이면 오류(일 목록의 오타를 시험이 잡는다)."""
    return RULES[rule](text, now, request_id)


def check(task: Any, text: str, now: datetime) -> tuple[str | None, list[str]]:
    """일의 모든 단계를 채워 본다. 돌려주는 값: (되물을 문장 또는 None, 안내에 붙일 읽은 방법들)."""
    notes: list[str] = []
    for step in task.steps:
        if not step.fill:
            continue
        result = fill(step.fill, text, now)
        if result.ask:
            return result.ask, []
        if result.note:
            notes.append(result.note)
    return None, notes
