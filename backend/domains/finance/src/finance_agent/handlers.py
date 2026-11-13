"""finance skill 핸들러.

| skill | 등급 | 요청자 | 하는 일 |
| --- | --- | --- | --- |
| `closing_report` | read | orchestrator, data | ① 기간 손익 요약 (`closing.py`) |
| `answer_planning_question` | read | planning | 기획 질문에 답함 (`planning_questions.py`) |
| `record_campaign_result` | read | marketing, orchestrator, data | 캠페인 손익 기록·판단·의견 |
| `review_campaign_budget` | read | marketing, orchestrator | 캠페인 예산 승인·조건부·거절 판단 |
| `verify_campaign_budget` | read | safety | 관문 `start_campaign` 검증 |
| `finance_insights` | read | orchestrator | 재무 인사이트와 근거 |
| `cancel_campaign` | read | marketing, orchestrator | 시작한 캠페인 취소, 잡아 둔 예산 풀기 |

`verify_amount`·`review_hold`는 재무 재설계(2026-10-23)로, `loss_aggregation`은 2026-10-27에
없앴다.
장애 비용은 평상시 장부로 들어가 `closing_report`에서 드러난다. 고객사 클레임 건별 확인과
`review_threshold_change`(AI가 기준 변경을 판단)도 재무 일이 아니라 없앴다.
재무는 쿠폰·환불의 끝난 금액을 장부에 기록만 한다.

- 숫자는 전부 코드로 계산한다. 요청 쪽이 적은 총액·등급은 믿지 않는다.
- 외부 입력(`request.untrusted`)은 읽지 않는다.
- 마켓운영(판매자 입점 심사·정산 보류)은 재무 일이 아니라 없앴다(2026-10-30).
- 판매자 정산(정산금 계산·지급 승인 요청·묶음 상태·지급 기한)은 고객지원이 한다(2026-11-12).
  재무는 그 결과를 받아(`settlement_feed.py`) 보여 주고 회계에만 쓴다.
- 기록은 메모리에 두고 `FINANCE_STATE_FILE`이 있으면 파일에도 남긴다
  (`persist.py`, 다시 켜도 이어짐).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ValidationError

from ax_agent_template import call_agent
from ax_agent_template.config import is_mock
from ax_contracts.schemas import (
    AgentRequest,
    AgentResult,
    EventEnvelope,
    EventType,
    Evidence,
    RequestMeta,
    TaskStatus,
)
from ax_contracts.schemas.finance import (
    CampaignBudgetReviewInput,
    CampaignBudgetReviewOutput,
    CampaignCancelInput,
    CampaignResultInput,
    FinanceOpinion,
    VerifyAmountInput,
)
from ax_contracts.schemas.planning import AnswerPlanningQuestionInput
from finance_agent import books as bk
from finance_agent import campaign as cp
from finance_agent import chat_log, closing, tax, team_budget
from finance_agent import finance_config as fc
from finance_agent import ledger as lg
from finance_agent.books import Books, load_books, off, set_pg_upload
from finance_agent.planning_questions import answer_question
from finance_agent.rules import load_rules
from finance_agent.schemas import (
    ClosingReportInput,
    ClosingReportOutput,
    SupportCostInput,
    SupportCostOutput,
    TeamBudgetConfirmInput,
    TeamBudgetConfirmOutput,
)
from finance_agent.sources import Impact, LiveSources, MockSources, Sources, order_key

__all__ = ["HANDLERS", "SUBSCRIBED_EVENTS", "on_event", "reset_state", "state"]


class Ledger:
    """이벤트로 쌓는 지급·환불 기록과 처리 결과."""

    def __init__(self) -> None:
        self.coupons: dict[str, dict[str, int]] = {}  # context_id → {"count", "total"}
        self.refunds: dict[str, int] = {}  # order_id → 환불 누적
        self.seen_events: set[str] = set()
        # 문의 번호 → 고객지원 처리 결과 비용 기록
        # 문의 비용 기록. 키는 바뀌지 않는 문의 ID(`inquiry_ref`)가 있으면 그것, 없으면 문의 번호
        self.support_costs: dict[int | str, dict[str, Any]] = {}
        # context_id → 사건 구간·부품. 총괄 사건 목록은 재시작하면 비워져서 받았을 때 남겨 둔다
        self.incident_windows: dict[str, dict[str, Any]] = {}
        self.coupon_events: dict[str, list[dict[str, Any]]] = {}  # context_id → 보상 쿠폰 이벤트
        # context_id → 총괄이 장애 이벤트 때 부른 재무 장부 기록 (`record_incident_finance`)
        self.incident_reports: dict[str, dict[str, Any]] = {}

    def compensation(self, context_id: str) -> dict[str, int]:
        return self.coupons.get(context_id, {"count": 0, "total": 0})


OPINION_CALL_SECONDS = 5.0
MAX_OPINIONS = 500


async def send_opinion(opinion: dict[str, Any], meta: RequestMeta) -> str | None:
    """총괄 skill `receive_finance_opinion`을 부른다. 전달했으면 None, 못 했으면 이유."""
    deadline = datetime.now(UTC) + timedelta(seconds=OPINION_CALL_SECONDS)
    request = AgentRequest(
        meta=meta.model_copy(update={"deadline": deadline}),
        skill="receive_finance_opinion",
        input=opinion,
    )
    result = await call_agent("orchestrator", request)
    return (
        None if result.status == TaskStatus.COMPLETED else (result.outcome or result.status.value)
    )


class State:
    def __init__(self, sources: Sources | None = None) -> None:
        self.sources: Sources = sources or (MockSources() if is_mock("finance") else LiveSources())
        self.ledger = Ledger()
        self.last_self_test: dict[str, Any] | None = None  # 마지막 "재무 AI 테스트" 결과
        self.closing_drifts: list[dict[str, Any]] = []  # 확정 뒤 달라진 결산. 하루 한 번 갱신
        self.vat_drifts: list[dict[str, Any]] = []  # 확정 뒤 달라진 부가세 분기
        self.last_daily_closing: dict[str, Any] | None = None  # 마지막 자동 결산(전날) 결과
        self.records = FinanceRecords()
        self.books_loader: Callable[[], Awaitable[Books]] = load_books  # 테스트에서 바꾼다
        self.opinion_sender: Callable[[dict[str, Any], RequestMeta], Awaitable[str | None]] = (
            send_opinion
        )
        # 시뮬레이션은 시나리오의 값을 넣는다. None이면 장부로 계산한다
        self.cash_override: int | None = None
        self.budget_override: dict[str, Any] | None = None
        self.repeat_override: dict[str, Any] | None = None
        self.started_override: dict[str, dict[str, Any]] | None = None
        # 사건별 쿠폰 장부(⑨)의 실행 기록. 테스트에서 바꾼다
        self.executions_loader: Callable[[str | None], Awaitable[lg.ExecutionFeed]] = (
            lg.load_executions
        )

    async def books(self) -> Books:
        return await self.books_loader()

    async def fresh_books(self) -> Books:
        """사람이 결정하는 일에 쓴다. 직전 장부로 답하지 않고 새로 읽은 것을 기다린다."""
        if self.books_loader is load_books:
            return await load_books(fresh=True)
        return await self.books_loader()


class FinanceRecords:
    """사람의 결정 기록. `FINANCE_STATE_FILE`이 있으면 `persist.py`가 파일에 남긴다."""

    def __init__(self) -> None:
        self.closings: dict[tuple[str, str], dict[str, Any]] = {}  # (period, date) → {by, at}
        self.closing_cancels: list[dict[str, Any]] = []  # 취소한 결산 확정 기록
        self.other_pnl: dict[str, dict[str, Any]] = {}  # YYYY-MM → 사람이 입력한 장부 밖 손익
        self.vat_filings: dict[str, dict[str, Any]] = {}  # 분기 시작일 → 확정한 부가세 숫자
        self.vat_filing_cancels: list[dict[str, Any]] = []
        self.recon_notes: dict[str, str] = {}  # payment_id → 처리 메모
        self.recon_note_log: list[dict[str, Any]] = []
        # 사람이 재무 화면에서 기획에 보낸 개선 요청 (`POST /api/finance/planning-requests`).
        # 기획이 되물으면(`answer_planning_question`) 이 기록의 근거로 답한다
        self.planning_requests: list[dict[str, Any]] = []
        # 예전 저장 파일의 정산 묶음(정산을 고객지원으로 옮기기 전 기록). 쓰지 않고, 옮기는
        # 스크립트가 읽을 수 있게 저장 파일에 그대로 둔다. 지금 묶음 상태는 고객지원에서 받는다
        self.legacy_batches: dict[str, dict[str, Any]] = {}
        # 없앤 예산·한도 경보의 확인 메모. 쓰지 않지만 사람이 쓴 기록이라 저장 파일에 그대로 둔다
        self.policy_acks: dict[str, dict[str, Any]] = {}
        self.bank_rows: list[dict[str, Any]] = []  # 사람이 올린 은행 거래내역 (출금은 음수)
        self.bank_out_periods: list[dict[str, str]] = []  # 출금 열이 있던 내역의 기간
        self.bank_notes: dict[str, dict[str, Any]] = {}  # 설명 없는 출금 id → 사람 메모
        self.bank_note_log: list[dict[str, Any]] = []
        self.campaigns: dict[str, dict[str, Any]] = {}  # 캠페인 id → 손익 기록
        # 관문에서 읽은 start_campaign 실행 기록. 관문은 메모리라 재시작해도 잊지 않게 옮겨 둔다
        self.campaign_starts: dict[str, dict[str, Any]] = {}  # 실행 id → 기록
        self.campaign_cancels: dict[str, dict[str, Any]] = {}  # 캠페인 id → 취소 기록
        self.budget_reviews: list[dict[str, Any]] = []  # 예산 판단 기록
        self.lessons: list[dict[str, Any]] = []  # 실패 교훈
        self.opinions: list[dict[str, Any]] = []  # 총괄에 낸 의견과 전달 상태
        self.exports: dict[str, dict[str, Any]] = {}  # 자동으로 만든 파일 묶음
        self.bank_imports: list[dict[str, Any]] = []  # 은행 내역 폴더에서 읽은 기록
        # 올린 PG 정산 파일(`pg_file.py`): 결제 번호 → 줄, 덮는 판매일 기간, 올린 기록
        self.pg_rows: dict[str, dict[str, Any]] = {}
        self.pg_periods: list[dict[str, str]] = []
        self.pg_imports: list[dict[str, Any]] = []
        self.pg_version = 0  # 올린 자료가 바뀔 때마다 하나씩 (장부 계산 다시 하기)
        self.team_budgets: dict[str, dict[str, Any]] = {}  # YYYY-MM → 재무 담당이 확정한 팀 예산


STATE: State | None = None


def state() -> State:
    global STATE
    if STATE is None:
        STATE = State()
    return STATE


def _pg_upload() -> tuple[object, dict[str, dict[str, Any]], list[dict[str, str]]]:
    rec = state().records
    return (id(rec), rec.pg_version), rec.pg_rows, rec.pg_periods


set_pg_upload(_pg_upload)


def reset_state(sources: Sources | None = None) -> None:
    """테스트 사이에 저장소를 비운다."""
    global STATE
    STATE = State(sources)


# ---------------------------------------------------------------- 공통
def _now() -> datetime:
    return datetime.now(UTC)


def _evidence(queries: list[str], summary: str, ref_ids: list[str] | None = None) -> Evidence:
    return Evidence(
        queries=queries, data_summary=summary, collected_at=_now(), ref_ids=ref_ids or []
    )


def _result(request: AgentRequest, status: TaskStatus, **kwargs: Any) -> AgentResult:
    return AgentResult(meta=request.meta, status=status, **kwargs)


def _parse[T: BaseModel](request: AgentRequest, model: type[T]) -> T | AgentResult:
    try:
        return model.model_validate(request.input)
    except ValidationError as error:
        errors = [f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()]
        return _result(
            request, TaskStatus.FAILED, outcome="invalid_input", output={"errors": errors}
        )


def _positive_int(value: Any) -> int | None:
    ok = isinstance(value, int) and not isinstance(value, bool) and value > 0
    return value if ok else None


# ---------------------------------------------------------------- 이벤트 (지급·환불 기록)
async def on_event(envelope: EventEnvelope) -> None:
    """`coupon.issued`(보상 쿠폰)와 `payment.refunded`를 누적한다. 같은 event_id는 한 번만."""
    ledger = state().ledger
    if envelope.event_id in ledger.seen_events:
        return
    ledger.seen_events.add(envelope.event_id)
    data = envelope.data
    if envelope.type == EventType.COUPON_ISSUED and envelope.context_id:
        if data.get("purpose_code") != "compensation":
            return
        count, total = _positive_int(data.get("count")), _positive_int(data.get("total_value"))
        if count is None or total is None:
            return
        row = ledger.coupons.setdefault(envelope.context_id, {"count": 0, "total": 0})
        row["count"] += count
        row["total"] += total
        ledger.coupon_events.setdefault(envelope.context_id, []).append(
            lg.coupon_from_event(envelope.context_id, data, envelope.occurred_at)
        )
        chat_log.ledger_recorded(
            envelope.context_id, envelope.tenant_id, envelope.source, "coupon", total, count
        )
    elif envelope.type == EventType.PAYMENT_REFUNDED:
        order_id, amount = order_key(data.get("order_id")), _positive_int(data.get("refund_amount"))
        if order_id is not None and amount is not None:
            ledger.refunds[order_id] = ledger.refunds.get(order_id, 0) + amount
            chat_log.ledger_recorded(
                envelope.context_id, envelope.tenant_id, envelope.source, "refund", amount
            )


SUBSCRIBED_EVENTS = [EventType.COUPON_ISSUED, EventType.PAYMENT_REFUNDED]


# ---------------------------------------------------------------- 사건 보상 쿠폰 공용
async def compensation_ledger(
    context_id: str, feed: lg.ExecutionFeed | None = None
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """한 사건의 보상 쿠폰 장부 줄과 출처 (`ledger.merge`)."""
    if feed is None:
        feed = await state().executions_loader(context_id)
    event_rows = state().ledger.coupon_events.get(context_id, [])
    return lg.merge(context_id, feed, event_rows, lg.events_connected())


def incident_meta(context_id: str) -> dict[str, Any]:
    """사건의 장애 구간·부품 중 재무가 혼자 아는 것. (목 모드) 시드 → 모름.

    실제 모드에서는 모름이다. 구간을 알려 주던 총괄의 `loss_aggregation` 요청을 2026-10-27에
    없앴다. 실제 모드 구간은 `incident_info`가
    총괄 사건 목록을 읽어서 채운다.
    """
    if is_mock("finance"):
        seeded = bk.seed_incidents().get(context_id)
        if seeded:
            return seeded | {"category": None}
    return {"component": None, "window_from": None, "window_to": None, "category": None}


_WINDOW_TIMES = ("window_from", "window_to", "recovered_at")


async def incident_info(context_id: str) -> dict[str, Any]:
    """사건의 장애 구간·부품. 목 모드는 시드, 실제 모드는 총괄 사건 목록(읽기 전용).

    총괄에게 사건 **중**에 묻는 것이 아니다 — 평상시 사건 조회에서만 읽는다
    ([협업] #417 제안 A, 요청 #418, 계약 `data_access/orchestrator_incidents.py`).
    못 받으면 모름(None)이고 추정값을 만들지 않는다(fail-closed).
    """
    info = incident_meta(context_id)
    if info["window_from"] and info["window_to"]:
        return info
    found = await state().sources.incident_window(context_id)
    if found and found.get("window_from") and found.get("window_to"):
        state().ledger.incident_windows[context_id] = {
            k: fc.iso(v) if isinstance(v, datetime) else v
            for k, v in found.items()
            if v is not None
        }
    elif not found:
        saved = state().ledger.incident_windows.get(context_id)
        if not saved:
            return info
        # 시각은 글자로 저장돼 있다. 글자 그대로 쓰면 사건 화면 전체가 500이 된다
        found = saved | {k: fc.parse_dt(saved.get(k)) for k in saved if k in _WINDOW_TIMES}
    return info | {k: v for k, v in found.items() if v is not None}


async def team_budget_data(month: str) -> tuple[dict[str, Any], list[str]]:
    """그 달 팀 예산(인사 인원 × 인당 월 단가)과 못 받은 자료 이름."""
    meta = RequestMeta(
        context_id=f"ctx_team_budget_{month}",
        token="finance-dashboard",
        deadline=_now() + timedelta(seconds=15),
    )
    now_out = await state().sources.team_headcount(meta, month)
    prev_out = await state().sources.team_headcount(meta, team_budget.shift_month(month, -1))

    def heads(out: Any) -> dict[str, int] | None:
        if out is None:
            return None
        return team_budget.counts([(r.team, r.headcount) for r in out.rows])

    data = team_budget.report(
        month,
        heads(now_out),
        heads(prev_out),
        fc.load_config().team_budget.per_head_monthly,
        state().records.team_budgets.get(month),
        basis=now_out.basis if now_out else None,
        example=bool(now_out and now_out.example),
    )
    missing = ["team_headcount"] if now_out is None else []
    if now_out is not None and prev_out is None:
        missing.append("team_headcount_prev")
    return data, missing


async def incident_impact(
    context_id: str, meta: RequestMeta | None = None, info: dict[str, Any] | None = None
) -> Impact | None:
    """data `impact_analysis` 결과 그대로(다시 계산하지 않음). 구간을 모르면 None.

    `info`를 받으면 사건 구간을 다시 조회하지 않는다 (`incident_view`가 한 번만 읽게).
    """
    if info is None:
        info = await incident_info(context_id)
    window = None
    if info["window_from"] and info["window_to"]:
        window = {
            "window_from": fc.iso(info["window_from"]),
            "window_to": fc.iso(info["window_to"]),
            **({"category": info["category"]} if info.get("category") else {}),
        }
    base = meta or RequestMeta(
        context_id=context_id, token="finance-dashboard", deadline=_now() + timedelta(seconds=15)
    )
    impact = await state().sources.impact(
        base.model_copy(update={"context_id": context_id}),
        context_id,
        window,
        final=bool(info.get("final")),  # 해결로 닫힌 사건은 한 번 받은 영향을 계속 쓴다
    )
    if impact is not None and info.get("window_exact") is False:
        impact = dataclasses.replace(impact, window_exact=False)
    return impact


# ---------------------------------------------------------------- closing_report (읽기)
BOOK_QUERIES = [
    "shop-api:/api/shop/orders",
    "shop-api:/api/shop/coupons",
    "safety:/api/safety/executions",
]


def closing_key(period: str, d: Any) -> tuple[str, str]:
    """결산 확정 기록의 키. 주는 그 주 월요일, 월은 그 달 1일로 맞춘다."""
    start, _ = closing.period_range(period, d)  # type: ignore[arg-type]
    return period, (d if period == "day" else start).isoformat()


async def closing_report(request: AgentRequest) -> AgentResult:
    """① 기간 손익 요약. 대시보드 `GET /api/finance/closing`과 같은 계산이다."""
    data = _parse(request, ClosingReportInput)
    if isinstance(data, AgentResult):
        return data
    books = await state().books()
    d = data.date or fc.today()
    key = closing_key(data.period, d)
    report = await closing.closing_report(
        books, data.period, d, state().records.closings.get(key), state().records.closings
    )
    output = ClosingReportOutput(
        **{k: v for k, v in report.items() if k not in ("from", "to")},
        window_from=report["from"],
        window_to=report["to"],
        unavailable=books.meta()["unavailable"],
    )
    return _result(
        request,
        TaskStatus.COMPLETED,
        outcome="partial" if output.unavailable else "reported",
        output=output.model_dump(mode="json"),
        evidence=_evidence(
            [*BOOK_QUERIES[:2], f"closing:{data.period}:{key[1]}"], report["commentary"]
        ),
    )


# ------------------------------------------------- record_team_budget (인사 확정 → 재무 기록)
async def confirm_team_budget(
    month: str, signature: str, by: str, note: str | None
) -> tuple[dict[str, Any] | None, str, str]:
    """인사가 확정한 팀 예산을 장부에 남긴다. (기록, 결과 코드, 안내) — 기록이 None이면 거절.

    예산 확정은 인사 AI(사람)가 한다(11/13). 재무는 지금 숫자가 확정할 때 보던 숫자와 같은지만
    보고 기록한다. 확정은 지금 인원과 맞춰 본다(화면용으로 기억해 둔 인원이면 그사이 확정된 발령을
    놓친다).
    """
    state().sources.forget_headcount(month)
    data, _ = await team_budget_data(month)
    if data["status"] == "unavailable":
        return None, "team_headcount_unavailable", "인사 AI에서 팀 인원을 받지 못해 기록할 수 없다"
    if data["example"]:
        return None, "example_headcount", "인사 AI가 준 인원이 가상 명부(예시)라 기록하지 않는다"
    if data["signature"] != signature:
        return None, "stale", "그사이 인원이나 단가가 바뀌었다. 다시 불러와 확인하고 확정한다"
    saved = state().records.team_budgets.get(month)
    if saved and saved.get("signature") == signature and saved.get("decided_by") == "hr":
        return saved, "already_recorded", "같은 숫자로 이미 기록했다"
    record = team_budget.approval(data, by, fc.iso(_now()), note) | {"decided_by": "hr"}
    state().records.team_budgets[month] = record
    return record, "recorded", "인사가 확정한 팀 예산을 기록했다"


async def record_team_budget(request: AgentRequest) -> AgentResult:
    """인사 AI에서 사람이 확정한 팀 예산을 재무 장부에 기록한다 (hr → finance, 11/13).

    돈을 움직이지 않고 재무 기록만 남긴다(그래서 읽기 등급이다). 숫자가 바뀌었거나 예시 인원이면
    기록하지 않고 실패로 돌려준다.
    """
    data = _parse(request, TeamBudgetConfirmInput)
    if isinstance(data, AgentResult):
        return data
    note = (data.note or "").strip() or None
    record, code, message = await confirm_team_budget(
        data.month, data.signature, data.confirmed_by.strip(), note
    )
    if record is None:
        return _result(request, TaskStatus.FAILED, outcome=code, output={"message": message})
    output = TeamBudgetConfirmOutput(
        month=data.month, status=code, total=record["total"], signature=record["signature"]
    )
    return _result(
        request,
        TaskStatus.COMPLETED,
        outcome=code,
        output=output.model_dump(mode="json"),
        evidence=Evidence(
            data_summary=f"{data.month} 팀 예산 {record['total']:,}원 (인사 확정 {record['by']})",
            collected_at=_now(),
            ref_ids=[f"team_budget:{data.month}"],
        ),
    )


# ------------------------------------------------- record_support_cost (읽기·장부 기록)
SUPPORT_COST_QUERIES = ["shop-api:/api/shop/orders"]


def _with_new_expense(
    record: dict[str, Any], data: SupportCostInput
) -> tuple[dict[str, Any], bool]:
    """이미 있는 기록에 나중에 끝난 환불·쿠폰을 반영한다. 처음 기록한 시각과 사건은 그대로 둔다.

    금액·쿠폰이 없는 이벤트(닫을 때 이벤트의 재전송 등)는 아무것도 바꾸지 않는다.
    """
    if data.refund_amount is None and data.coupon_id is None:
        return record, False
    refund = record["refund_amount"] if data.refund_amount is None else data.refund_amount
    coupon = record["coupon_issued"] or data.coupon_id is not None
    updated = record | {
        "resolution": data.resolution,
        "claim_decision": data.claim_decision or record["claim_decision"],
        "refund_amount": refund,
        "coupon_issued": coupon,
        "expense_used": refund > 0 or coupon,
    }
    if updated == record:
        return record, False
    return updated | {"updated_at": _now().isoformat()}, True


async def record_support_cost(request: AgentRequest) -> AgentResult:
    """고객지원이 끝낸 처리 결과(환불 금액·쿠폰)를 받아 주문 배송비와 함께 재무 장부에 적는다.

    환불 여부를 확인·검증하지 않는다. 받은 금액을 그대로 적는다.

    돈을 움직이거나 외부 상태를 바꾸지 않는다(그래서 읽기 등급이다). 경비를 쓰지 않은 문의
    (답변만 한 문의, 환불·쿠폰이 없는 문의)도 상담 처리 결과를 기록으로 남긴다.
    같은 문의는 기록 하나만 둔다. 문의를 닫은 뒤 환불이 끝나면 고객지원이 금액을 채워 한 번 더
    보내므로(`refund_amount`는 그 문의의 환불 금액 전체) 그때는 기록을 갱신한다. 금액·쿠폰이 없는
    재전송은 기록을 바꾸지 않는다. 주문을 장부에서 찾지 못하면 배송비를 비우고 이유를 적는다.
    """
    data = _parse(request, SupportCostInput)
    if isinstance(data, AgentResult):
        return data
    ledger = state().ledger
    unavailable: list[str] = []
    shipping_fee: int | None = None
    if data.order_id is not None:
        books = await state().books()
        order = books.order(data.order_id)
        if order is not None:
            shipping_fee = int(order.get("shipping_fee") or 0)
        else:
            unavailable.append(f"장부에 주문 {data.order_id}가 없어 배송비를 확인하지 못함")
        unavailable.extend(books.meta([])["unavailable"])
    else:
        unavailable.append("문의에 주문이 연결되지 않아 배송비를 확인하지 못함")
    # 번호만으로 찾으면 번호가 다시 매겨진 새 문의가 예전 기록을 덮어쓴다 (11/13). 그래서 바뀌지
    # 않는 문의 ID가 오면 그것으로 기록을 구분한다. 예전 기록(번호 키)은 그대로 둔다
    key: int | str = data.inquiry_ref or data.inquiry_id
    previous = ledger.support_costs.get(key)
    if previous is None:
        refund = data.refund_amount or 0
        coupon = data.coupon_id is not None
        record = {
            "record_id": f"sc_{data.inquiry_ref or data.inquiry_id}",
            "inquiry_id": data.inquiry_id,
            "inquiry_ref": data.inquiry_ref,
            "order_id": data.order_id,
            "resolution": data.resolution,
            "claim_decision": data.claim_decision,
            "refund_amount": refund,
            "coupon_issued": coupon,
            "expense_used": refund > 0 or coupon,
            "shipping_fee": shipping_fee,
            "context_id": request.meta.context_id,
            "recorded_at": _now().isoformat(),
        }
        status = "recorded"
    else:
        record, changed = _with_new_expense(previous, data)
        status = "updated" if changed else "already_recorded"
    ledger.support_costs[key] = record
    spent = "경비 사용 없음"
    if record["expense_used"]:
        parts = [f"환불 {record['refund_amount']:,}원"] if record["refund_amount"] else []
        spent = ", ".join(parts + (["쿠폰 지급"] if record["coupon_issued"] else []))
    note = f"문의 #{data.inquiry_id} 처리 결과 기록: {data.resolution}, {spent}"
    output = SupportCostOutput(
        record_id=record["record_id"],
        inquiry_id=data.inquiry_id,
        order_id=record["order_id"],
        status=status,
        resolution=record["resolution"],
        claim_decision=record["claim_decision"],
        refund_amount=record["refund_amount"],
        coupon_issued=record["coupon_issued"],
        expense_used=record["expense_used"],
        shipping_fee=record["shipping_fee"],
        note=note,
        unavailable=unavailable,
    )
    refs = [f"inquiry:{data.inquiry_id}"] + ([f"order:{data.order_id}"] if data.order_id else [])
    return _result(
        request,
        TaskStatus.COMPLETED,
        outcome="partial" if unavailable else "recorded",
        output=output.model_dump(mode="json"),
        evidence=_evidence(SUPPORT_COST_QUERIES, note, refs),
    )


async def answer_planning_question(request: AgentRequest) -> AgentResult:
    """기획이 되묻는 근거 질문에 재무가 보낸 요청의 기록으로만 답한다(읽기). 없으면 `unknown`."""
    data = _parse(request, AnswerPlanningQuestionInput)
    if isinstance(data, AgentResult):
        return data
    out = answer_question(data, request.meta.tenant_id, request.meta.context_id)
    return _result(
        request,
        TaskStatus.COMPLETED,
        outcome=out.status,
        output=out.model_dump(mode="json"),
        evidence=Evidence(
            queries=[f"finance.sent_requests request_id={data.request_id}"],
            data_summary=f"{data.question_id} → {out.status}",
            collected_at=datetime.now(UTC),
            ref_ids=out.evidence_ids,
        ),
    )


# ------------------------------------------------- 캠페인 재무
def _opinion_meta(context_id: str, opinion_id: str) -> RequestMeta:
    return RequestMeta(
        context_id=context_id,
        token="finance",
        deadline=_now() + timedelta(seconds=OPINION_CALL_SECONDS),
        idempotency_key=f"{context_id}:finance_opinion:{opinion_id}",
    )


async def submit_opinion(opinion: dict[str, Any], context_id: str) -> dict[str, Any]:
    """의견을 보관하고 총괄에 보낸다. 못 보내면 "전달 대기"로 두고 나중에 다시 보낸다."""
    FinanceOpinion.model_validate(opinion)
    opinions = state().records.opinions
    for row in opinions:
        if row["opinion"]["opinion_id"] == opinion["opinion_id"]:
            return row
    row = {"opinion": opinion, "context_id": context_id, "status": "pending", "attempts": 0}
    opinions.append(row)
    del opinions[:-MAX_OPINIONS]
    await _deliver(row)
    return row


async def _deliver(row: dict[str, Any]) -> None:
    row["attempts"] += 1
    meta = _opinion_meta(row["context_id"], row["opinion"]["opinion_id"])
    try:
        problem = await state().opinion_sender(row["opinion"], meta)
    except Exception as exc:  # 총괄이 꺼져 있어도 재무 판단은 끝난다
        problem = type(exc).__name__
    if problem is None:
        row |= {"status": "delivered", "delivered_at": fc.iso(fc.now()), "last_problem": None}
    else:
        row["last_problem"] = str(problem)[:200]


async def retry_opinions() -> int:
    """전달 대기 의견을 다시 보낸다. 전달한 수."""
    done = 0
    for row in state().records.opinions:
        if row["status"] == "pending":
            await _deliver(row)
            done += row["status"] == "delivered"
    return done


async def cash_net_krw() -> int | None:
    """앞으로 몇 주의 누적 순현금. 주문 자료를 못 받았거나 빈 주가 있으면 None(확인 불가)."""
    if state().cash_override is not None:
        return state().cash_override
    books = await state().books()
    if "orders" in books.unavailable:
        return None
    from finance_agent.finance_api import cashflow_view

    weeks = load_rules().brake.cash_lookahead_weeks
    rows = (await cashflow_view(books))["weeks"][:weeks]
    values = [r.get("net") for r in rows]
    if len(values) < weeks or any(v is None for v in values):
        return None
    return sum(values)


async def started_campaigns() -> dict[str, dict[str, Any]] | None:
    """캠페인별 시작 금액. 기준은 안전 관문 실행 기록이다. 못 읽으면 None(모른다고 답한다)."""
    if state().started_override is not None:
        return state().started_override
    records = state().records
    try:
        feed = await state().executions_loader(None)
    except Exception:  # 관문이 꺼져 있으면 쓴 돈을 모른다
        return None
    if feed.rows is None:
        return None
    for row in feed.rows:
        cid = (row.get("params") or {}).get("campaign_id")
        amount = row.get("computed_amount")
        if row.get("action") != "start_campaign" or row.get("status") != "executed":
            continue
        if isinstance(cid, str) and cid and isinstance(amount, int):
            records.campaign_starts[row["execution_id"]] = {
                "campaign_id": cid,
                "amount_krw": amount,
                "at": fc.iso(row["created_at"]),
            }
    return cp.summarize_starts(list(records.campaign_starts.values()), records.campaign_cancels)


async def monthly_budget() -> dict[str, Any]:
    if state().budget_override is not None:
        return state().budget_override
    books = await state().books()
    return await off(
        cp.budget_from_last_month,
        books,
        fc.today(),
        state().records.closings,
        state().records.other_pnl,
    )


async def repeat_estimate() -> dict[str, Any] | None:
    if state().repeat_override is not None:
        return state().repeat_override
    return await off(cp.repeat_estimate, await state().books(), fc.today())


async def review_budget(data: CampaignBudgetReviewInput, context_id: str) -> dict[str, Any]:
    """예산 판단. 판단을 남기고, 승인이 아니면 총괄에 의견으로 알린다."""
    records = state().records
    today = fc.today()
    review = cp.decide(
        data.model_dump(),
        campaigns=records.campaigns,
        reviews=records.budget_reviews,
        lessons=records.lessons,
        cash_net_krw=await cash_net_krw(),
        today=today,
        monthly_budget=await monthly_budget(),
        started=await started_campaigns(),
    )
    review |= {
        "review_id": f"bud_{data.campaign_id}_{len(records.budget_reviews) + 1}",
        "reviewed_at": fc.iso(fc.now()),
        "context_id": context_id,
    }
    records.budget_reviews.append(review)
    if review["decision"] != "approved":
        await submit_opinion(cp.budget_opinion(review), context_id)
    return review


def _review_output(review: dict[str, Any]) -> dict[str, Any]:
    """재무는 의견만 낸다. 요청 쪽이 막지 않게 decision은 approved·한도는 요청액으로 주고,
    재무 의견은 opinion·suggested_krw·reasons·recommendation에 담는다(계약 `advisory`)."""
    return CampaignBudgetReviewOutput(
        review_id=review["review_id"],
        campaign_id=review["campaign_id"],
        decision="approved",
        requested_krw=review["requested_krw"],
        approved_limit_krw=review["requested_krw"],
        reasons=review["reasons"],
        recommendation=review["recommendation"],
        applied_rules=review["applied_rules"],
        advisory=True,
        opinion=cp.OPINION_OF[review["decision"]],
        suggested_krw=review["approved_limit_krw"],
    ).model_dump(mode="json")


async def review_campaign_budget(request: AgentRequest) -> AgentResult:
    """마케팅의 캠페인 예산 요청에 재무 의견을 낸다. 막지 않는다. 쿠폰 발급·환불은 대상이 아니다."""
    data = _parse(request, CampaignBudgetReviewInput)
    if isinstance(data, AgentResult):
        return data
    review = await review_budget(data, request.meta.context_id)
    return _result(
        request,
        TaskStatus.COMPLETED,
        outcome=cp.OPINION_OF[review["decision"]],
        output=_review_output(review),
        evidence=_evidence(
            ["finance.campaigns", "finance.lessons", "finance:cashflow"],
            f"{data.campaign_id} {cp.DECISION_KO[review['decision']]}",
            [review["review_id"]],
        ),
    )


async def verify_campaign_budget(request: AgentRequest) -> AgentResult:
    """예전 관문 검증 skill. 재무는 비용을 승인·반려하지 않으므로 관문은 더 부르지 않는다.

    계약상 skill은 남겨 두고, 불리면 막지 않고(approved) 재무 의견을 근거로 돌려준다.
    """
    data = _parse(request, VerifyAmountInput)
    if isinstance(data, AgentResult):
        return data
    params, amount = data.params, data.computed_amount
    try:
        ask = CampaignBudgetReviewInput(
            campaign_id=params.get("campaign_id"),
            campaign_name=params.get("campaign_name"),
            channel=params.get("channel"),
            requested_krw=amount,
        )
    except ValidationError:
        return _result(
            request,
            TaskStatus.COMPLETED,
            outcome="rejected",
            output={"decision": "rejected", "reason_code": "invalid_params"},
        )
    # 먼저 받은 판단은 그사이 다른 캠페인이 시작했으면 틀릴 수 있어 시작 직전에 다시 판단한다
    review = await review_budget(ask, request.meta.context_id)
    return _result(
        request,
        TaskStatus.COMPLETED,
        outcome="approved",
        output={
            "decision": "approved",
            "reason_code": "finance_advisory_only",
            "review_id": review["review_id"],
            "opinion": cp.OPINION_OF[review["decision"]],
            "suggested_krw": review["approved_limit_krw"],
            "reasons": review["reasons"],
        },
        evidence=_evidence(
            ["finance.budget_reviews"], f"{ask.campaign_id} 검증", [review["review_id"]]
        ),
    )


async def record_result(data: CampaignResultInput, context_id: str) -> dict[str, Any]:
    """손익 기록 → 교훈 반영 → 의견·인사이트 제출. 같은 내용을 다시 보내면 그대로 돌려준다."""
    records = state().records
    body = data.model_dump()
    previous = records.campaigns.get(data.campaign_id)
    if previous is not None and all(previous.get(k) == v for k, v in body.items()):
        return previous | {"write": "already_recorded"}
    started = await started_campaigns()
    if started is None:
        return {"status": "starts_unavailable", "campaign_id": data.campaign_id}
    if data.campaign_id not in started:
        return {"status": "not_started", "campaign_id": data.campaign_id}
    record = cp.compute_result(body, await repeat_estimate()) | {
        "over_started_krw": cp.over_started(body, started[data.campaign_id]),
        "revision": (previous or {}).get("revision", 0) + 1,
        "recorded_at": fc.iso(fc.now()),
        "context_id": context_id,
    }
    records.campaigns[data.campaign_id] = record
    if record["verdict"] is None:
        return record | {"write": "recorded"}
    record["lesson"] = cp.apply_lessons(record, records.lessons, _now())
    await submit_opinion(cp.opinion_of(record, _now()), context_id)
    await submit_insights(context_id)
    return record | {"write": "recorded"}


async def current_insights() -> list[dict[str, Any]]:
    records = state().records
    books = await state().books()
    # 곧 신고할 분기는 지금 진행 중인 분기가 아니라 직전 분기다
    start, _, _ = tax.quarter_range(fc.today())
    pack = (
        None
        if "orders" in books.unavailable
        else await off(tax.vat_pack, books, start - timedelta(days=1))
    )
    return cp.insights(records.campaigns, records.budget_reviews, pack, fc.today())


async def submit_insights(context_id: str) -> list[dict[str, Any]]:
    """새 인사이트를 총괄에 낸다. 같은 인사이트는 한 번만 낸다."""
    found = await current_insights()
    for item in found:
        await submit_opinion(
            {
                "opinion_id": f"ins_{item['insight_id']}",
                "kind": "insight",
                "verdict": item["kind"],
                "reasons": [f"{e['label']}: {e['value']}" for e in item["evidence"]],
                "recommendation": f"{item['title']}. {item['recommendation']}",
                "applied_rules": item["applied_rules"],
                "submitted_at": fc.iso(fc.now()),
            },
            context_id,
        )
    return found


async def record_campaign_result(request: AgentRequest) -> AgentResult:
    """캠페인 성과를 손익으로 장부에 적고 효율을 판단해 총괄에 의견을 낸다."""
    data = _parse(request, CampaignResultInput)
    if isinstance(data, AgentResult):
        return data
    record = await record_result(data, request.meta.context_id)
    if record["status"] in ("not_started", "starts_unavailable"):
        return _result(request, TaskStatus.FAILED, outcome=record["status"], output=record)
    return _result(
        request,
        TaskStatus.COMPLETED,
        outcome=record["status"],
        output=record,
        evidence=_evidence(
            ["finance.campaigns", f"source:{data.source}"],
            f"{data.campaign_id} 손익 {record['profit_krw']}",
            [data.campaign_id],
        ),
    )


async def cancel_budget(data: CampaignCancelInput) -> dict[str, Any]:
    """시작한 캠페인을 취소해 잡아 둔 예산을 푼다. 결과가 기록된 캠페인은 취소하지 않는다."""
    records = state().records
    if data.campaign_id in records.campaigns:
        return {"campaign_id": data.campaign_id, "status": "already_recorded", "released_krw": 0}
    started = await started_campaigns()
    if started is None:
        return {"campaign_id": data.campaign_id, "status": "starts_unavailable", "released_krw": 0}
    row = started.get(data.campaign_id)
    if row is None or row["active_krw"] == 0:
        return {"campaign_id": data.campaign_id, "status": "not_started", "released_krw": 0}
    records.campaign_cancels[data.campaign_id] = {
        "campaign_id": data.campaign_id,
        "reason": data.reason,
        "canceled_at": fc.iso(fc.now()),
        "released_krw": row["active_krw"],
    }
    return {
        "campaign_id": data.campaign_id,
        "status": "canceled",
        "released_krw": row["active_krw"],
    }


async def cancel_campaign(request: AgentRequest) -> AgentResult:
    """캠페인 취소. 재무 기록만 바꾼다. 광고를 멈추는 일은 요청 쪽이 한다."""
    data = _parse(request, CampaignCancelInput)
    if isinstance(data, AgentResult):
        return data
    out = await cancel_budget(data)
    done = out["status"] == "canceled"
    return _result(
        request,
        TaskStatus.COMPLETED if done else TaskStatus.FAILED,
        outcome=out["status"],
        output=out,
        evidence=_evidence(["finance.campaign_starts"], f"{data.campaign_id} {out['status']}"),
    )


async def finance_insights(request: AgentRequest) -> AgentResult:
    found = await current_insights()
    return _result(
        request,
        TaskStatus.COMPLETED,
        outcome="reported",
        output={"insights": found},
        evidence=_evidence(["finance.campaigns", "finance:tax"], f"인사이트 {len(found)}건"),
    )


HANDLERS = {
    "record_campaign_result": record_campaign_result,
    "review_campaign_budget": review_campaign_budget,
    "verify_campaign_budget": verify_campaign_budget,
    "finance_insights": finance_insights,
    "cancel_campaign": cancel_campaign,
    "closing_report": closing_report,
    "record_support_cost": record_support_cost,
    "record_team_budget": record_team_budget,
    "answer_planning_question": answer_planning_question,
}
