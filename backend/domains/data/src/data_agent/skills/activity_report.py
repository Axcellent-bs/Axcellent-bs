"""activity_report skill: 부서별 AI 업무 리포트 (데이터분석 AI 탭).

각 부서 AI가 무슨 일을 했고 어떻게 끝났는지를 숫자로 낸다. 숫자는 전부 코드가 센다.

- 공통 로그는 읽기만 한다(`clients.log_client`). 부서 숫자는 `metrics.activity`가 센다.
- 회사 숫자:
  - 총거래액·수수료 매출: 재무 `closing_report`(A2A).
    재무 마감은 UTC 날짜 기준이고 day·month만 받는다.
    주간은 하루씩 7번 불러 더한다. 한국 시간 기간과 9시간 어긋난다는 것을 `note`에 적는다.
  - 주문 수·결제 성공률: shop-api `/api/shop/metric-series` (`order_count`, `payment_count`,
    `payment_fail_rate`). 다른 부서 DB는 직접 열지 않는다.
  - 사건 수: `kpi_summary`와 같은 정의로 공통 로그에서 센다 (`ctx_inc_` context).
- 일간 저장(`report_store`, `DATA_REPORT_DB`): 하루치 "더할 수 있는 값"을 저장해 두고, 저장된 날은
  그 값을, 오늘·저장 안 된 날은 바로 센 값을 쓴다. 주간·월간은 하루치를 더한 뒤 비율·평균·중앙값을
  다시 계산한다(평균의 평균이 아니다). 저장은 `monitor.snapshot_tick`이 한다.
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
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path

from ax_agent_template import routing
from ax_agent_template.llm import LLMClient
from ax_agent_template.services import SERVICES, department_of
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
    SkillCount,
    UnknownSection,
    UnusedHandoff,
)
from data_agent.clients.log_client import LogSource
from data_agent.clients.shop_client import ShopUnavailable
from data_agent.detect import MetricSource
from data_agent.explain import explain
from data_agent.labels import department_label, skill_label
from data_agent.metrics.activity import (
    _PRIORITY,
    MIN_DENOMINATOR,
    SMALL_SAMPLE,
    ActivityCounts,
    Scope,
    count_activity,
    finish_activity,
)
from data_agent.report_store import ReportStore, Snapshot

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
FINANCE_UNUSED = "재무 AI를 쓰지 않아 확인할 수 없음"
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
def enabled_service_ids(enabled: Callable[[str], bool] = routing.is_enabled) -> list[str]:
    """이 회사가 켠(AI를 쓰는) 서비스 id. 백엔드 `AX_ENABLED_SERVICES` 기준(비어 있으면 전부)."""
    return [s.id for s in SERVICES if s.skills and enabled(s.id)]


def scope_for(services: list[str], allowed: Iterable[str] | None = None) -> Scope:
    """서비스 id → 부서별 서비스.

    `allowed`(켠 서비스)가 있으면 그 안에서만 센다.
    비우면 켠 서비스 전체이고, 켠 서비스가 아닌 id는 뺀다.
    """
    known = {s.id: s for s in SERVICES}
    unknown = [s for s in services if s not in known]
    if unknown:
        raise InvalidActivityInput(f"모르는 서비스 id: {', '.join(unknown)}")
    usable = {s.id for s in SERVICES if s.skills} if allowed is None else set(allowed)
    if services:
        chosen = [known[s] for s in services if s in usable]
        if not chosen:
            raise InvalidActivityInput(f"이 회사가 AI를 쓰지 않는 서비스: {', '.join(services)}")
    else:
        chosen = [s for s in SERVICES if s.id in usable]
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
def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days)]


def _runs(days: list[date]) -> list[tuple[date, date]]:
    """이어진 날짜끼리 묶은 (시작, 끝 다음 날) 목록."""
    runs: list[tuple[date, date]] = []
    for d in sorted(days):
        if runs and runs[-1][1] == d:
            runs[-1] = (runs[-1][0], d + timedelta(days=1))
        else:
            runs.append((d, d + timedelta(days=1)))
    return runs


def _window(piece: tuple[date, date]) -> tuple[datetime, datetime]:
    return _kst_midnight(piece[0]), _kst_midnight(piece[1])


async def _logs(
    source: LogSource, piece: tuple[date, date], tenant_id: str
) -> tuple[list[LogRecord], bool]:
    """한 구간(한국 시간 날짜)을 한 번에 읽는다."""
    start, end = _window(piece)
    query = CommonLogQuery(from_=start, to=end, limit=QUERY_LIMIT, tenant_id=tenant_id)
    result = await source.query(query)
    return [r for r in result.records if start <= r.occurred_at < end], result.truncated


# -------------------------------------------------------------------------------- 회사 숫자
@dataclass(frozen=True)
class Figure:
    value: float | None
    reason: str | None = None  # 값이 없을 때 이유


@dataclass
class CompanyCounts:
    """회사 숫자의 더할 수 있는 값. 받지 못한 값은 None과 이유 (0으로 두지 않는다)."""

    gmv: int | None = 0
    commission_revenue: int | None = 0
    finance_reason: str | None = None
    finance_collected_at: str | None = None  # 재무 응답 시각(ISO). 여러 번이면 가장 이른 것
    orders: float | None = 0.0
    attempts: int | None = 0  # 결제 시도 수 (결제 성공률 분모)
    failed: int | None = 0  # 결제 실패 수
    shop_reason: str | None = None

    def merge(self, other: CompanyCounts) -> CompanyCounts:
        def add(a, b):  # noqa: ANN001, ANN202
            return None if a is None or b is None else a + b

        return CompanyCounts(
            gmv=add(self.gmv, other.gmv),
            commission_revenue=add(self.commission_revenue, other.commission_revenue),
            finance_reason=self.finance_reason or other.finance_reason,
            finance_collected_at=min(
                (x for x in (self.finance_collected_at, other.finance_collected_at) if x),
                default=None,
            ),
            orders=add(self.orders, other.orders),
            attempts=add(self.attempts, other.attempts),
            failed=add(self.failed, other.failed),
            shop_reason=self.shop_reason or other.shop_reason,
        )

    def to_json(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_json(cls, data: dict) -> CompanyCounts:
        return cls(**{k: data.get(k) for k in cls().__dict__})


@dataclass
class PeriodCounts:
    """한 구간(하루 또는 이어진 여러 날)의 더할 수 있는 값. 일간 저장소에 이 모양으로 둔다."""

    activity: ActivityCounts
    company: CompanyCounts
    # 그때 켠(AI를 쓰는) 서비스 id. 나중에 서비스를 더 켜도 지난 날의 부서·숫자가 바뀌지 않게 둔다
    enabled_services: list[str] = field(default_factory=list)

    def merge(self, other: PeriodCounts) -> PeriodCounts:
        return PeriodCounts(
            self.activity.merge(other.activity),
            self.company.merge(other.company),
            sorted(set(self.enabled_services) | set(other.enabled_services)),
        )

    @property
    def has_unknown(self) -> bool:
        """다시 만들 만한 확인할 수 없음 (재무·shop-api 실패, 조회 한도). 로그 0건은 아니다."""
        c = self.company
        return bool(c.finance_reason or c.shop_reason or self.activity.truncated)

    def to_json(self) -> dict:
        return {
            "activity": self.activity.to_json(),
            "company": self.company.to_json(),
            "enabled_services": list(self.enabled_services),
        }

    @classmethod
    def from_json(cls, data: dict) -> PeriodCounts:
        return cls(
            ActivityCounts.from_json(data.get("activity") or {}),
            CompanyCounts.from_json(data.get("company") or {}),
            [str(s) for s in data.get("enabled_services") or []],
        )


def _finance_dates(start: date, end: date) -> list[tuple[str, date]]:
    """재무에 물을 (period, UTC 날짜) 목록.

    한 달을 통째로 물으면 month 한 번, 월요일부터 7일이면 week 한 번(#190), 아니면 하루씩.
    """
    if start.day == 1 and end == _month_start(start, 1):
        return [("month", start)]
    if is_whole_week(start, end):
        return [("week", start)]
    return [("day", d) for d in _days(start, end)]


def is_whole_week(start: date, end: date) -> bool:
    """월요일부터 일요일까지 7일인지 (재무 `period=week`와 같은 범위)."""
    return start.weekday() == 0 and (end - start).days == 7


def _finance_note(period: str, start: date, end: date) -> str:
    last = end - timedelta(days=1)
    span = f"{start.year}년 {start.month}월" if period == "month" else f"{start}~{last}"
    return f"재무 마감 기준(UTC 날짜 {span}). 한국 시간 기간보다 9시간 늦게 시작하고 끝남"


FinanceAnswer = tuple[dict[str, int] | None, str | None, str | None]  # (금액, 이유, 응답 시각)


async def _ask_finance(send: SendFn, meta: RequestMeta, period: str, day: date) -> FinanceAnswer:
    deadline = min(meta.deadline, datetime.now(UTC) + timedelta(seconds=FINANCE_TIMEOUT))
    request = AgentRequest(
        meta=meta.model_copy(update={"deadline": deadline}),
        skill="closing_report",
        input={"period": period, "date": day.isoformat()},
    )
    try:
        result = await asyncio.wait_for(send("finance", request), timeout=FINANCE_TIMEOUT)
    except Exception as error:  # 보조 조회라 어떤 실패든 확인할 수 없음으로 남긴다
        return None, f"재무 closing_report 호출 실패 ({type(error).__name__})", None
    if result.status != TaskStatus.COMPLETED:
        if result.outcome == "service_disabled":
            return None, "재무 서비스가 꺼져 있어 closing_report를 받지 못함", None
        reason = f"재무 closing_report 응답 {result.status.value} ({result.outcome or '-'})"
        return None, reason, None
    at = result.evidence.collected_at.isoformat() if result.evidence.collected_at else None
    output = result.output or {}
    unavailable = [str(u) for u in output.get("unavailable") or []]
    lost = [u for u in unavailable if u in REVENUE_INPUTS]
    if lost:  # 장부에 주문·상품이 없으면 매출을 믿을 수 없다 (예전 재무는 0으로 계산했다)
        return None, f"재무 장부에 {', '.join(lost)} 자료가 없어 매출을 확인할 수 없음", at
    amounts: dict[str, int] = {}
    for line in output.get("lines") or []:
        if not isinstance(line, dict) or line.get("code") not in ("gmv", "commission_revenue"):
            continue
        if line.get("amount") is None:  # 재무가 확인할 수 없다고 답함 (#190, 0원과 다르다)
            why = f" ({', '.join(unavailable)})" if unavailable else ""
            return None, f"재무가 {line.get('code')}를 확인할 수 없다고 답함{why}", at
        if isinstance(line.get("amount"), int):
            amounts[str(line["code"])] = line["amount"]
    if "gmv" not in amounts or "commission_revenue" not in amounts:
        return None, "재무 closing_report에 gmv·commission_revenue 줄이 없음", at
    return amounts, None, at


class FinanceAsker:
    """한 요청(또는 한 번의 저장 작업) 안의 재무 closing_report 호출.

    (period, 날짜)마다 한 번만 부르고(이번·직전 기간이 겹쳐도 다시 부르지 않는다),
    동시에 `FINANCE_CONCURRENCY`개까지만 보낸다. 재무는 호출마다 쇼핑몰 주문 전체를 다시 읽어서,
    한꺼번에 많이 보내면 재무·shop-api가 줄을 서고 시간 초과로 모두 실패한다.
    """

    def __init__(
        self,
        send: SendFn,
        meta: RequestMeta,
        limit: int = FINANCE_CONCURRENCY,
        use_finance: bool = True,
    ) -> None:
        self.send, self.meta = send, meta
        # 재무 AI를 쓰지 않는 회사면 부르지 않는다.
        # 꺼진 서비스로 가면 사람 할 일함에 쌓이기 때문이다
        self.use_finance = use_finance
        self._limit = asyncio.Semaphore(limit)
        self._asked: dict[tuple[str, date], asyncio.Task] = {}
        self.calls = 0  # 실제로 보낸 호출 수

    def ask(self, period: str, day: date) -> asyncio.Task:
        key = (period, day)
        if key not in self._asked:
            self._asked[key] = asyncio.ensure_future(self._run(period, day))
        return self._asked[key]

    async def _run(self, period: str, day: date) -> FinanceAnswer:
        if not self.use_finance:
            return None, FINANCE_UNUSED, None
        async with self._limit:
            self.calls += 1
            return await _ask_finance(self.send, self.meta, period, day)


async def finance_counts(asker: FinanceAsker, start: date, end: date) -> CompanyCounts:
    """한 구간의 총거래액·수수료 매출. 하루라도 받지 못하면 None과 그 이유."""
    answers = await asyncio.gather(*(asker.ask(p, d) for p, d in _finance_dates(start, end)))
    at = min((when for _, _, when in answers if when), default=None)
    reason = next((why for _, why, _ in answers if why), None)
    if reason:
        return CompanyCounts(
            gmv=None, commission_revenue=None, finance_reason=reason, finance_collected_at=at
        )
    return CompanyCounts(
        gmv=sum(a["gmv"] for a, _, _ in answers if a),
        commission_revenue=sum(a["commission_revenue"] for a, _, _ in answers if a),
        finance_collected_at=at,
    )


async def finance_figures(
    asker: FinanceAsker, period: str, start: date, end: date
) -> CompanyCounts:
    """예전 이름 (`period`는 구간으로 정해져서 쓰지 않는다)."""
    del period
    return await finance_counts(asker, start, end)


async def shop_counts(source: MetricSource, piece: tuple[date, date]) -> CompanyCounts:
    """한 구간의 주문 수·결제 시도·실패 (shop-api 5분 버킷 합)."""
    start, end = _window(piece)
    try:
        orders = await source.fetch("order_count", start, end)
        counts = dict(await source.fetch("payment_count", start, end))
        fail_rates = dict(await source.fetch("payment_fail_rate", start, end))
    except ShopUnavailable as error:
        reason = f"shop-api 지표를 받지 못함 [{error.code}]"
        return CompanyCounts(orders=None, attempts=None, failed=None, shop_reason=reason)
    return CompanyCounts(
        orders=float(sum(v for _, v in orders)),
        attempts=int(sum(counts.values())),
        failed=sum(round(c * fail_rates.get(ts, 0.0)) for ts, c in counts.items()),
    )


async def live_counts(
    pieces: list[tuple[date, date]],
    logs: LogSource,
    metrics: MetricSource,
    asker: FinanceAsker,
    tenant_id: str,
    skip_finance: set[tuple[date, date]] | None = None,
    enabled: Callable[[str], bool] = routing.is_enabled,
) -> list[PeriodCounts]:
    """구간마다 바로 센다. 이어진 구간은 공통 로그를 한 번에 읽고 메모리에서 나눈다.

    shop-api 지표를 먼저 받는다. 재무 호출이 shop-api에서 주문 전체를 읽는 동안 줄 서지 않게.
    `skip_finance` 구간은 재무를 묻지 않는다(주 단위로 한 번에 묻는 주간 리포트).
    """
    skip = skip_finance or set()
    runs = _runs([d for piece in pieces for d in _days(*piece)])
    read = await asyncio.gather(*(_logs(logs, run, tenant_id) for run in runs))
    shops = await asyncio.gather(*(shop_counts(metrics, piece) for piece in pieces))
    finances = await asyncio.gather(
        *(_no_finance() if piece in skip else finance_counts(asker, *piece) for piece in pieces)
    )
    out: list[PeriodCounts] = []
    for piece, shop, finance in zip(pieces, shops, finances, strict=True):
        start, end = _window(piece)
        records: list[LogRecord] = []
        truncated = False
        for run, (run_records, cut) in zip(runs, read, strict=True):
            if run[0] <= piece[0] and piece[1] <= run[1]:
                records = [r for r in run_records if start <= r.occurred_at < end]
                truncated = cut
        counts = count_activity(records, truncated).split_unused(enabled)
        out.append(PeriodCounts(counts, shop.merge(finance), enabled_service_ids(enabled)))
    return out


async def _no_finance() -> CompanyCounts:
    return CompanyCounts()


async def build_day_snapshot(
    day: date,
    meta: RequestMeta,
    logs: LogSource,
    metrics: MetricSource,
    send: SendFn,
    now: datetime | None = None,
    enabled: Callable[[str], bool] = routing.is_enabled,
) -> Snapshot:
    """한국 시간 하루의 저장할 값을 만든다 (LLM을 쓰지 않는다).

    만들 때 켠 서비스 목록을 같이 저장한다. 재무 AI를 쓰지 않으면 재무를 부르지 않는다.

    재무 마감은 UTC 날짜라 그 UTC 날짜가 아직 끝나지 않았으면(한국 시간 다음 날 09시 전)
    매출을 확인할 수 없음으로 두고, 다음 날 한 번 다시 만든다.
    """
    now = now or datetime.now(UTC)
    asker = FinanceAsker(send, meta, use_finance=enabled("finance"))
    (counts,) = await live_counts(
        [(day, day + timedelta(days=1))], logs, metrics, asker, meta.tenant_id, enabled=enabled
    )
    utc_day_end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=UTC)
    if asker.use_finance and now < utc_day_end and counts.company.finance_reason is None:
        counts.company = CompanyCounts.from_json(
            counts.company.to_json()
            | {
                "gmv": None,
                "commission_revenue": None,
                "finance_reason": "재무 마감 기준 UTC 날짜가 아직 끝나지 않아 다음 날 다시 받음",
            }
        )
    return Snapshot(
        tenant_id=meta.tenant_id,
        day=day,
        created_at=now,
        payload=counts.to_json(),
        has_unknown=counts.has_unknown,
    )


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
UNUSED_TOP_SKILLS = 3


def unused_handoffs(counts: ActivityCounts) -> list[UnusedHandoff]:
    """AI를 쓰지 않는 부서로 넘긴 일: 부서별 건수와 업무(skill 한국어 이름) 상위 3개.

    건수만 센다. "도입하면 자동 처리" 같은 예측 문구·숫자는 만들지 않는다.
    """
    by_dept: dict[str, list[str]] = {}
    for service in counts.unused_handed:
        by_dept.setdefault(department_of(service), []).append(service)
    out: list[UnusedHandoff] = []
    for dept, services in by_dept.items():
        skills: Counter[str] = Counter()
        for service in services:
            skills.update(counts.unused_skills.get(service, {}))
        top = sorted(skills.items(), key=lambda kv: (-kv[1], kv[0]))[:UNUSED_TOP_SKILLS]
        out.append(
            UnusedHandoff(
                department=dept,
                label=department_label(dept),
                count=sum(counts.unused_handed[s] for s in services),
                top_skills=[SkillCount(skill=s, label=skill_label(s), count=n) for s, n in top],
            )
        )
    return sorted(out, key=lambda u: (-u.count, u.department))


@dataclass(frozen=True)
class ActivityResult:
    output: ActivityReportOutput
    evidence: Evidence
    fallback: bool
    fallback_reason: str | None = None
    fallback_numbers: tuple[float, ...] = ()


def _company_figures(c: CompanyCounts) -> dict[str, Figure]:
    """더한 회사 값 → 칸별 값. 결제 성공률은 더한 시도·실패로 다시 계산한다."""
    attempts, failed = c.attempts, c.failed
    if attempts is None or failed is None:
        success = Figure(None, c.shop_reason)
    elif attempts < MIN_DENOMINATOR:
        success = Figure(None, f"결제 시도 {attempts}건: {SMALL_SAMPLE}")
    else:
        success = Figure(round((attempts - failed) / attempts, 4))
    return {
        "gmv": Figure(None if c.gmv is None else float(c.gmv), c.finance_reason),
        "commission_revenue": Figure(
            None if c.commission_revenue is None else float(c.commission_revenue),
            c.finance_reason,
        ),
        "order_count": Figure(c.orders, c.shop_reason if c.orders is None else None),
        "payment_success_rate": success,
    }


def _figure(
    now: Figure,
    before: Figure,
    source: str,
    note: str | None = None,
    collected_at: str | None = None,
) -> CompanyFigure:
    return CompanyFigure(
        value=now.value,
        previous_period=before.value,
        source=source,
        note=note,
        collected_at=datetime.fromisoformat(collected_at) if collected_at else None,
    )


def _merge(parts: list[PeriodCounts]) -> PeriodCounts:
    total = PeriodCounts(ActivityCounts(), CompanyCounts())
    for part in parts:
        total = total.merge(part)
    return total


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
    store: ReportStore | None = None,
) -> ActivityResult:
    """리포트를 만든다. 저장된 날은 저장값을, 오늘·저장 안 된 날은 바로 센 값을 더한다.

    주간·월간은 하루치 값(분자·분모, 합계·개수, 목록)을 더한 뒤 비율·평균·중앙값을 다시 계산한다.
    저장소가 없으면 전부 바로 센다(기간마다 공통 로그를 한 번에 읽는다).
    """
    started = now or datetime.now(UTC)
    p = periods(data.period, data.start, started)
    scope_for(data.services)  # 모르는 서비스 id는 저장소를 읽기 전에 거절한다
    window, previous = p.window(), p.window(previous=True)
    tenant = meta.tenant_id
    today = started.astimezone(KST).date()
    cur_days, prev_days = _days(p.start, p.end), _days(p.previous_start, p.start)

    saved = store.get_many(tenant, [d for d in prev_days + cur_days if d < today]) if store else {}
    if store is None:
        pieces = [(p.previous_start, p.start), (p.start, p.end)]
    else:
        pieces = _runs([d for d in prev_days if d not in saved]) + _runs(
            [d for d in cur_days if d not in saved]
        )
    # 부서 범위: 저장된 날은 그날 켠 서비스, 바로 계산하는 날은 지금 켠 서비스 (합집합)
    now_enabled = enabled_service_ids(enabled)
    allowed = set(now_enabled) if pieces else set()
    for snap in saved.values():
        allowed |= set(PeriodCounts.from_json(snap.payload).enabled_services)
    scope = scope_for(data.services, allowed)
    asker = FinanceAsker(send, meta, use_finance=enabled("finance"))

    # 주간: 7일이 다 저장돼 있고 매출이 다 있으면 저장된 재무 값의 합.
    # 아니면(저장 안 된 날, 또는 저장 때 매출을 확인할 수 없던 날이 있으면) 재무 week 한 번
    def saved_revenue(d: date) -> bool:
        snap = saved.get(d)
        return snap is not None and (snap.payload.get("company") or {}).get("gmv") is not None

    weekly = [
        days
        for days in (prev_days, cur_days)
        if data.period == "week"
        and is_whole_week(days[0], days[-1] + timedelta(days=1))
        and not all(saved_revenue(d) for d in days)
    ]
    skip = {piece for piece in pieces if any(piece[0] in days for days in weekly)}
    live = await live_counts(
        pieces, logs, metrics, asker, tenant, skip_finance=skip, enabled=enabled
    )
    week_finance = dict(
        zip(
            [days[0] for days in weekly],
            await asyncio.gather(
                *(finance_counts(asker, days[0], days[-1] + timedelta(days=1)) for days in weekly)
            ),
            strict=True,
        )
    )

    def total(days: list[date]) -> PeriodCounts:
        inside = set(days)
        parts = [PeriodCounts.from_json(saved[d].payload) for d in days if d in saved]
        parts += [c for piece, c in zip(pieces, live, strict=True) if piece[0] in inside]
        merged = _merge(parts)
        week = week_finance.get(days[0]) if days else None
        if week is not None:  # 주 단위 재무 값으로 바꾼다 (저장된 날의 일간 재무 값은 쓰지 않는다)
            merged.company.gmv = week.gmv
            merged.company.commission_revenue = week.commission_revenue
            merged.company.finance_reason = week.finance_reason
            merged.company.finance_collected_at = week.finance_collected_at
        return merged

    cur_counts, prev_counts = total(cur_days), total(prev_days)
    current = finish_activity(cur_counts.activity, scope)
    before = finish_activity(prev_counts.activity, scope)
    fin = _company_figures(cur_counts.company)
    prev_fin = _company_figures(prev_counts.company)

    unknown: list[UnknownSection] = []

    def missing(section: str, reason: str) -> None:
        unknown.append(UnknownSection(section=section, reason=reason))

    note = _finance_note(data.period, p.start, p.end)
    finance_at = cur_counts.company.finance_collected_at  # 이번 기간 재무 응답 시각
    for key in ("gmv", "commission_revenue", "order_count", "payment_success_rate"):
        for label, figures in (("company", fin), ("company.previous_period", prev_fin)):
            if figures[key].value is None and figures[key].reason:
                missing(f"{label}.{key}", figures[key].reason or "")
    company = ActivityCompany(
        gmv=_figure(fin["gmv"], prev_fin["gmv"], FINANCE_SOURCE, note, finance_at),
        commission_revenue=_figure(
            fin["commission_revenue"],
            prev_fin["commission_revenue"],
            FINANCE_SOURCE,
            note,
            finance_at,
        ),
        order_count=_figure(fin["order_count"], prev_fin["order_count"], SHOP_SOURCE),
        payment_success_rate=_figure(
            fin["payment_success_rate"], prev_fin["payment_success_rate"], SHOP_SOURCE
        ),
        incident_count=CompanyFigure(
            value=float(current.incident_count),
            previous_period=float(before.incident_count),
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
    log_count, prev_log_count = cur_counts.activity.log_count, prev_counts.activity.log_count
    if not log_count:
        missing("departments", "이 기간 공통 로그 0건 (AI를 거친 일이 없음)")
    if not prev_log_count:
        missing("departments.previous_period", "직전 기간 공통 로그 0건")
    for section, cut in (
        ("departments", cur_counts.activity.truncated),
        ("departments.previous_period", prev_counts.activity.truncated),
    ):
        if cut:
            missing(section, f"공통 로그 조회 한도 {QUERY_LIMIT}건에 걸려 최근 기록만 셈")

    highlights = pick_highlights(company, departments)
    explained = None
    if highlights:
        explained = await explain("activity", highlight_facts(highlights), llm)
        summary, source = explained.summary, ("template" if explained.fallback else "llm")
    else:
        summary, source = NOTHING_NOTABLE, "none"

    stored_days = len(saved)
    live_days = len(prev_days) + len(cur_days) - stored_days
    snapshot_at = min((s.created_at for s in saved.values()), default=started)
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
        snapshot_at=snapshot_at,
        stored_days=stored_days,
        live_days=live_days,
        snapshot_note=f"저장된 날 {stored_days}일 / 바로 계산 {live_days}일",
        enabled_services=sorted(allowed),
        unused_handoffs=unused_handoffs(cur_counts.activity),
    )
    lines = [
        summary,
        f"공통 로그 {log_count}건 (직전 {prev_log_count}건)",
        output.snapshot_note or "",
    ]
    if explained and (line := explained.note()):
        lines.append(line)
    lines += [f"확인할 수 없음: {u.section}: {u.reason}" for u in unknown]
    evidence = Evidence(
        queries=[
            f"common_log from={previous[0].isoformat()} to={window[1].isoformat()}",
            f"{FINANCE_SOURCE} period={data.period}",
            f"{SHOP_SOURCE} order_count,payment_count,payment_fail_rate",
            f"data_reports days={stored_days}",
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
