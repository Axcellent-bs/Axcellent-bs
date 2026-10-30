"""activity_report skill: 부서별 AI 업무 리포트 (데이터분석 AI 탭).

각 부서 AI가 무슨 일을 했고 어떻게 끝났는지를 숫자로 낸다. 숫자는 전부 코드가 센다.

- 공통 로그는 읽기만 한다(`clients.log_client`). 부서 숫자는 `metrics.activity`가 센다.
- 회사 숫자:
  - 총거래액·수수료 매출: 재무 `closing_report`(A2A).
    재무 마감은 UTC 날짜 기준이고 day·month만 받는다.
    주간은 하루씩 7번 불러 더한다. 한국 시간 기간과 9시간 어긋난다는 것을 `note`에 적는다.
  - 주문 수·결제 성공률: shop-api `/api/shop/metric-series` (`order_count`, `payment_count`,
    `payment_fail_rate`). 다른 부서 DB는 직접 열지 않는다.
  - 사건 수: `kpi_summary`와 같은 계산(`metrics.kpi.compute_kpi`)을 공통 로그에 돌린다.
- 특이사항(highlights): 직전 기간 대비 변화율이 큰 건수·금액 항목 최대 3개.
  직전 값이 3 미만이면 뺀다.
- 요약: 특이사항으로 LLM이 3줄을 쓴다. 숫자 검증과 템플릿 대체 이유는 `explain`과 같다.
  특이사항이 없으면 LLM을 부르지 않고 "특이사항 없음"이다.
- 사람이 직접 한 일(로그에 없는 일)은 세지 않는다. "절약한 시간" 같은 추정치는 만들지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path

from ax_agent_template import routing
from ax_agent_template.llm import LLMClient
from ax_agent_template.services import SERVICES
from ax_contracts.data_access.common_log import CommonLogQuery, LogRecord
from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, RequestMeta, TaskStatus
from ax_contracts.schemas.data import (
    ActivityCompany,
    ActivityHighlight,
    ActivityReportInput,
    ActivityReportOutput,
    CompanyFigure,
    DepartmentActivity,
    DepartmentReport,
    UnknownSection,
)
from data_agent.clients.log_client import LogSource
from data_agent.clients.shop_client import ShopUnavailable
from data_agent.detect import MetricSource
from data_agent.explain import explain
from data_agent.labels import department_label
from data_agent.metrics.activity import (
    _PRIORITY,
    MIN_DENOMINATOR,
    SMALL_SAMPLE,
    Scope,
    compute_activity,
)
from data_agent.metrics.kpi import compute_kpi

KST = timezone(timedelta(hours=9))
QUERY_LIMIT = 5000  # 공통 로그 조회 계약 상한
FINANCE_TIMEOUT = 15.0  # 재무 호출 한 번. 동시 개수 상한 안에서 보낼 때부터 잰다
FINANCE_CONCURRENCY = 2  # 재무 closing_report 동시 호출 상한
# 재무 장부에서 빠지면 총거래액·수수료 매출을 믿을 수 없는 자료 (그때 재무는 0으로 계산한다).
# 클레임·쿠폰·PG 정산은 이 두 줄 계산에 쓰지 않아서 빠져도 그대로 쓴다
REVENUE_INPUTS = ("orders", "orders_partial", "products")
MAX_HIGHLIGHTS = 3
NOTHING_NOTABLE = "특이사항 없음"
FINANCE_SOURCE = "finance.closing_report"
SHOP_SOURCE = "shop-api.metric-series"
KPI_SOURCE = "data.kpi_summary(공통 로그)"

SendFn = Callable[[str, AgentRequest], Awaitable[AgentResult]]

OUTCOME_KO = {
    "auto_completed": "자동 완료",
    "human_approved": "사람 확인 후 실행",
    "held": "보류",
    "rejected": "거절",
    "unknown": "확인할 수 없음",
    "failed": "실패",
}


class InvalidActivityInput(ValueError):
    """입력이 틀렸다 (모르는 서비스 id 등)."""


# ------------------------------------------------------------------------------------- 기간
@dataclass(frozen=True)
class Periods:
    start: date  # 한국 시간, 포함
    end: date  # 한국 시간, 제외
    previous_start: date

    def window(self, previous: bool = False) -> tuple[datetime, datetime]:
        a, b = (self.previous_start, self.start) if previous else (self.start, self.end)
        return _kst_midnight(a), _kst_midnight(b)


def _kst_midnight(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=KST).astimezone(UTC)


def _month_start(d: date, shift: int = 0) -> date:
    index = d.year * 12 + d.month - 1 + shift
    return date(index // 12, index % 12 + 1, 1)


def periods(period: str, start: date | None, now: datetime) -> Periods:
    """기간과 직전 기간. 시작일을 비우면 끝난 직전 기간(어제·지난주 월~일·지난달)."""
    today = now.astimezone(KST).date()
    if period == "day":
        s = start or today - timedelta(days=1)
        return Periods(s, s + timedelta(days=1), s - timedelta(days=1))
    if period == "week":
        s = start or today - timedelta(days=today.weekday() + 7)
        return Periods(s, s + timedelta(days=7), s - timedelta(days=7))
    s = _month_start(start) if start else _month_start(today, -1)
    return Periods(s, _month_start(s, 1), _month_start(s, -1))


# ------------------------------------------------------------------------------------- 범위
def scope_for(services: list[str]) -> Scope:
    """서비스 id → 부서별 서비스. 비우면 skill이 있는 서비스 전체."""
    known = {s.id: s for s in SERVICES}
    unknown = [s for s in services if s not in known]
    if unknown:
        raise InvalidActivityInput(f"모르는 서비스 id: {', '.join(unknown)}")
    chosen = [known[s] for s in services] if services else [s for s in SERVICES if s.skills]
    departments: dict[str, tuple[str, ...]] = {}
    for service in chosen:
        departments[service.department] = (*departments.get(service.department, ()), service.id)
    return Scope(departments)


def card_owner(service_id: str) -> str | None:
    """서비스 카드(`AX_SERVICE_CARDS_DIR`, 기본 backend/services)의 owner. 못 읽으면 None."""
    base = os.getenv(routing.CARDS_ENV) or str(
        Path(routing.__file__).resolve().parents[4] / "services"
    )
    try:
        card = json.loads((Path(base) / service_id / "agent-card.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None
    owner = card.get("owner") if isinstance(card, dict) else None
    return owner if isinstance(owner, str) and owner else None


# ---------------------------------------------------------------------------------- 공통 로그
async def _logs(
    source: LogSource, p: Periods, tenant_id: str
) -> tuple[list[LogRecord], list[LogRecord], bool]:
    """직전 기간 시작 ~ 이번 기간 끝을 한 번에 읽어 (이번, 직전)으로 나눈다."""
    (prev_start, start), (_, end) = p.window(previous=True), p.window()
    query = CommonLogQuery(from_=prev_start, to=end, limit=QUERY_LIMIT, tenant_id=tenant_id)
    result = await source.query(query)
    current = [r for r in result.records if start <= r.occurred_at < end]
    previous = [r for r in result.records if prev_start <= r.occurred_at < start]
    return current, previous, result.truncated


# -------------------------------------------------------------------------------- 회사 숫자
@dataclass(frozen=True)
class Figure:
    value: float | None
    reason: str | None = None  # 값이 없을 때 이유


def _finance_dates(period: str, start: date, end: date) -> list[tuple[str, date]]:
    """재무에 물을 (period, UTC 날짜) 목록. 주간은 하루씩."""
    if period == "month":
        return [("month", start)]
    return [("day", start + timedelta(days=i)) for i in range((end - start).days)]


def _finance_note(period: str, start: date, end: date) -> str:
    last = end - timedelta(days=1)
    span = f"{start.year}년 {start.month}월" if period == "month" else f"{start}~{last}"
    return f"재무 마감 기준(UTC 날짜 {span}). 한국 시간 기간보다 9시간 늦게 시작하고 끝남"


async def _ask_finance(
    send: SendFn, meta: RequestMeta, period: str, day: date
) -> tuple[dict[str, int] | None, str | None]:
    deadline = min(meta.deadline, datetime.now(UTC) + timedelta(seconds=FINANCE_TIMEOUT))
    request = AgentRequest(
        meta=meta.model_copy(update={"deadline": deadline}),
        skill="closing_report",
        input={"period": period, "date": day.isoformat()},
    )
    try:
        result = await asyncio.wait_for(send("finance", request), timeout=FINANCE_TIMEOUT)
    except Exception as error:  # 보조 조회라 어떤 실패든 확인할 수 없음으로 남긴다
        return None, f"재무 closing_report 호출 실패 ({type(error).__name__})"
    if result.status != TaskStatus.COMPLETED:
        if result.outcome == "service_disabled":
            return None, "재무 서비스가 꺼져 있어 closing_report를 받지 못함"
        return None, f"재무 closing_report 응답 {result.status.value} ({result.outcome or '-'})"
    output = result.output or {}
    lost = [u for u in output.get("unavailable") or [] if u in REVENUE_INPUTS]
    if lost:  # 장부에 주문·상품이 없으면 재무는 0으로 계산한다. 0을 쓰지 않는다
        return None, f"재무 장부에 {', '.join(lost)} 자료가 없어 매출을 확인할 수 없음"
    amounts: dict[str, int] = {}
    for line in output.get("lines") or []:
        if isinstance(line, dict) and isinstance(line.get("amount"), int):
            amounts[str(line.get("code"))] = line["amount"]
    if "gmv" not in amounts or "commission_revenue" not in amounts:
        return None, "재무 closing_report에 gmv·commission_revenue 줄이 없음"
    return amounts, None


class FinanceAsker:
    """한 요청 안의 재무 closing_report 호출.

    (period, 날짜)마다 한 번만 부르고(이번·직전 기간이 겹쳐도 다시 부르지 않는다),
    동시에 `FINANCE_CONCURRENCY`개까지만 보낸다. 재무는 호출마다 쇼핑몰 주문 전체를 다시 읽어서,
    한꺼번에 많이 보내면 재무·shop-api가 줄을 서고 시간 초과로 모두 실패한다.
    """

    def __init__(self, send: SendFn, meta: RequestMeta, limit: int = FINANCE_CONCURRENCY) -> None:
        self.send, self.meta = send, meta
        self._limit = asyncio.Semaphore(limit)
        self._asked: dict[tuple[str, date], asyncio.Task] = {}
        self.calls = 0  # 실제로 보낸 호출 수

    def ask(self, period: str, day: date) -> asyncio.Task:
        key = (period, day)
        if key not in self._asked:
            self._asked[key] = asyncio.ensure_future(self._run(period, day))
        return self._asked[key]

    async def _run(self, period: str, day: date) -> tuple[dict[str, int] | None, str | None]:
        async with self._limit:
            self.calls += 1
            return await _ask_finance(self.send, self.meta, period, day)


async def finance_figures(
    asker: FinanceAsker, period: str, start: date, end: date
) -> dict[str, Figure]:
    answers = await asyncio.gather(
        *(asker.ask(p, d) for p, d in _finance_dates(period, start, end))
    )
    reason = next((why for _, why in answers if why), None)
    if reason:
        return {"gmv": Figure(None, reason), "commission_revenue": Figure(None, reason)}
    return {
        code: Figure(float(sum(a[code] for a, _ in answers if a)))
        for code in ("gmv", "commission_revenue")
    }


async def shop_figures(
    source: MetricSource, window: tuple[datetime, datetime]
) -> dict[str, Figure]:
    start, end = window
    try:
        orders = await source.fetch("order_count", start, end)
        counts = dict(await source.fetch("payment_count", start, end))
        fail_rates = dict(await source.fetch("payment_fail_rate", start, end))
    except ShopUnavailable as error:
        reason = f"shop-api 지표를 받지 못함 [{error.code}]"
        return {"order_count": Figure(None, reason), "payment_success_rate": Figure(None, reason)}
    attempts = int(sum(counts.values()))
    failed = sum(round(c * fail_rates.get(ts, 0.0)) for ts, c in counts.items())
    success = (
        Figure(round((attempts - failed) / attempts, 4))
        if attempts >= MIN_DENOMINATOR
        else Figure(None, f"결제 시도 {attempts}건: {SMALL_SAMPLE}")
    )
    return {
        "order_count": Figure(float(sum(v for _, v in orders))),
        "payment_success_rate": success,
    }


# --------------------------------------------------------------------------------- 특이사항
def _candidates(company: ActivityCompany, departments: list[DepartmentReport]) -> list[tuple]:
    """(key, label, 이번, 직전). 건수·금액만 (비율은 분모가 달라 섞지 않는다)."""
    out: list[tuple[str, str, float | None, float | None]] = []
    for key, label in (
        ("gmv", "회사 총거래액"),
        ("commission_revenue", "회사 수수료 매출"),
        ("order_count", "회사 주문 수"),
        ("incident_count", "회사 사건 수"),
    ):
        figure: CompanyFigure = getattr(company, key)
        out.append((f"company.{key}", label, figure.value, figure.previous_period))
    for d in departments:
        now, before = d, d.previous_period

        def pair(get: Callable[[DepartmentActivity], int]) -> tuple[float, float]:
            return float(get(now)), float(get(before))  # noqa: B023 - 바로 쓴다

        base = f"departments.{d.department}"
        out.append(
            (f"{base}.requests.total", f"{d.label} 처리한 요청", *pair(lambda a: a.requests.total))
        )
        for name in _PRIORITY:
            out.append(
                (
                    f"{base}.outcomes.{name}",
                    f"{d.label} {OUTCOME_KO[name]}",
                    *pair(lambda a, n=name: getattr(a.outcomes, n).count),
                )
            )
        out.append(
            (
                f"{base}.handoffs.handed",
                f"{d.label} 사람에게 넘긴 일",
                *pair(lambda a: a.handoffs.handed),
            )
        )
        out.append(
            (
                f"{base}.incidents.count",
                f"{d.label} 참여한 사건",
                *pair(lambda a: a.incidents.count),
            )
        )
    return out


def pick_highlights(
    company: ActivityCompany, departments: list[DepartmentReport]
) -> list[ActivityHighlight]:
    """직전 대비 변화율이 큰 항목 최대 3개. 직전 값(분모)이 3 미만이거나 같으면 뺀다."""
    picked = []
    for key, label, current, previous in _candidates(company, departments):
        if current is None or previous is None or previous < MIN_DENOMINATOR or current == previous:
            continue
        change = (current - previous) / previous * 100
        picked.append((abs(change), abs(current - previous), key, label, current, previous, change))
    picked.sort(key=lambda p: (-p[0], -p[1], p[2]))
    return [
        ActivityHighlight(
            key=key, label=label, current=cur, previous=prev, change_percent=round(change, 1)
        )
        for _, _, key, label, cur, prev, change in picked[:MAX_HIGHLIGHTS]
    ]


def _num(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value


def highlight_facts(highlights: list[ActivityHighlight]) -> dict[str, int | float | str]:
    facts: dict[str, int | float | str] = {}
    for i, h in enumerate(highlights, start=1):
        facts[f"항목{i}"] = h.label
        facts[f"직전{i}"] = _num(h.previous)
        facts[f"이번{i}"] = _num(h.current)
        facts[f"변화율{i}"] = _num(abs(h.change_percent))
        facts[f"방향{i}"] = "증가" if h.change_percent > 0 else "감소"
    return facts


# ------------------------------------------------------------------------------------- 조립
@dataclass(frozen=True)
class ActivityResult:
    output: ActivityReportOutput
    evidence: Evidence
    fallback: bool
    fallback_reason: str | None = None
    fallback_numbers: tuple[float, ...] = ()


def _figure(now: Figure, before: Figure, source: str, note: str | None = None) -> CompanyFigure:
    return CompanyFigure(value=now.value, previous_period=before.value, source=source, note=note)


async def build_report(
    data: ActivityReportInput,
    meta: RequestMeta,
    llm: LLMClient,
    logs: LogSource,
    metrics: MetricSource,
    send: SendFn,
    now: datetime | None = None,
    enabled: Callable[[str], bool] = routing.is_enabled,
    owner: Callable[[str], str | None] = card_owner,
) -> ActivityResult:
    p = periods(data.period, data.start, now or datetime.now(UTC))
    scope = scope_for(data.services)
    window, previous = p.window(), p.window(previous=True)
    tenant = meta.tenant_id

    # shop-api 지표를 먼저 받는다. 재무 호출이 shop-api에서 주문 전체를 읽는 동안 줄 서지 않게
    (records, prev_records, cut), shop, prev_shop = await asyncio.gather(
        _logs(logs, p, tenant), shop_figures(metrics, window), shop_figures(metrics, previous)
    )
    prev_cut = cut
    kpi, prev_kpi = compute_kpi(records), compute_kpi(prev_records)
    mttr = {i.context_id: i.mttr_seconds for i in kpi.output.incidents}
    prev_mttr = {i.context_id: i.mttr_seconds for i in prev_kpi.output.incidents}
    current = compute_activity(records, scope, mttr)
    before = compute_activity(prev_records, scope, prev_mttr)

    unknown: list[UnknownSection] = []

    def missing(section: str, reason: str) -> None:
        unknown.append(UnknownSection(section=section, reason=reason))

    asker = FinanceAsker(send, meta)
    fin, prev_fin = await asyncio.gather(
        finance_figures(asker, data.period, p.start, p.end),
        finance_figures(asker, data.period, p.previous_start, p.start),
    )
    note = _finance_note(data.period, p.start, p.end)
    for key in ("gmv", "commission_revenue", "order_count", "payment_success_rate"):
        for label, figures in (
            ("company", fin | shop),
            ("company.previous_period", prev_fin | prev_shop),
        ):
            if figures[key].reason:
                missing(f"{label}.{key}", figures[key].reason or "")
    company = ActivityCompany(
        gmv=_figure(fin["gmv"], prev_fin["gmv"], FINANCE_SOURCE, note),
        commission_revenue=_figure(
            fin["commission_revenue"], prev_fin["commission_revenue"], FINANCE_SOURCE, note
        ),
        order_count=_figure(shop["order_count"], prev_shop["order_count"], SHOP_SOURCE),
        payment_success_rate=_figure(
            shop["payment_success_rate"], prev_shop["payment_success_rate"], SHOP_SOURCE
        ),
        incident_count=CompanyFigure(
            value=float(len(kpi.output.incidents)),
            previous_period=float(len(prev_kpi.output.incidents)),
            source=KPI_SOURCE,
        ),
    )

    departments: list[DepartmentReport] = []
    for dept, services in scope.departments.items():
        on = any(enabled(s) for s in services)
        owners = sorted({o for s in services if (o := owner(s))})
        departments.append(
            DepartmentReport(
                **current.departments[dept].model_dump(),
                department=dept,
                label=department_label(dept),
                owners=owners,
                services=list(services),
                enabled=on,
                previous_period=before.departments[dept],
            )
        )
        if not on:
            missing(
                f"departments.{dept}",
                "서비스가 꺼져 있어 AI 응답이 없음. 사람에게 넘긴 일(handoffs)만 셈",
            )
    for section, reason in current.notes:
        missing(section, reason)
    for section, reason in before.notes:
        missing(section.replace("departments.", "departments.previous_period.", 1), reason)
    if not records:
        missing("departments", "이 기간 공통 로그 0건 (AI를 거친 일이 없음)")
    if not prev_records:
        missing("departments.previous_period", "직전 기간 공통 로그 0건")
    for section, was_cut in (("departments", cut), ("departments.previous_period", prev_cut)):
        if was_cut:
            missing(section, f"공통 로그 조회 한도 {QUERY_LIMIT}건에 걸려 최근 기록만 셈")

    highlights = pick_highlights(company, departments)
    explained = None
    if highlights:
        explained = await explain("activity", highlight_facts(highlights), llm)
        summary, source = explained.summary, ("template" if explained.fallback else "llm")
    else:
        summary, source = NOTHING_NOTABLE, "none"

    output = ActivityReportOutput(
        period=data.period,
        start=p.start,
        end=p.end,
        window_from=window[0],
        window_to=window[1],
        previous_from=previous[0],
        previous_to=previous[1],
        company=company,
        departments=departments,
        collaboration=current.collaboration,
        highlights=highlights,
        summary=summary,
        summary_source=source,
        unknown_sections=unknown,
    )
    lines = [summary, f"공통 로그 {len(records)}건 (직전 {len(prev_records)}건)"]
    if explained and (line := explained.note()):
        lines.append(line)
    lines += [f"확인할 수 없음: {u.section}: {u.reason}" for u in unknown]
    evidence = Evidence(
        queries=[
            f"common_log from={window[0].isoformat()} to={window[1].isoformat()}",
            f"common_log from={previous[0].isoformat()} to={previous[1].isoformat()}",
            f"{FINANCE_SOURCE} period={data.period}",
            f"{SHOP_SOURCE} order_count,payment_count,payment_fail_rate",
        ],
        data_summary="\n".join(lines),
        collected_at=datetime.now(UTC),
    )
    return ActivityResult(
        output=output,
        evidence=evidence,
        fallback=bool(explained and explained.fallback),
        fallback_reason=explained.reason if explained else None,
        fallback_numbers=explained.bad_numbers if explained else (),
    )
