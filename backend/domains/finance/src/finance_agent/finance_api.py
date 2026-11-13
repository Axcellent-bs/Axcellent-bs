"""재무 대시보드 API (재무 재설계 6·11장). 응답은 `{data, meta}`, 실패는 `{error: {code, message}}`.

| 메서드·경로 | 하는 일 | 토큰 |
| --- | --- | --- |
| `GET /api/finance/overview` | 오늘 요약(KPI)과 사람이 할 일 | 없음 |
| `GET /api/finance/closing?period=&date=` | ① 손익·분개 미리보기·설명 | 없음 |
| `POST /api/finance/closing/confirm` | ① 결산 확정 기록. 날짜 필수, 끝난 기간만 | **사람** |
| `POST /api/finance/closing/cancel` | ① 잘못한 결산 확정 취소. 이유 필수, 기록이 남음 | **사람** |
| `POST /api/finance/closing/adjust` | ① 확정한 달의 차이를 전기 조정으로 반영 | **사람** |
| `POST /api/finance/tax/vat/confirm` | ⑤ 끝난 분기의 부가세 숫자 확정 | **사람** |
| `POST /api/finance/tax/vat/cancel` | ⑤ 잘못한 부가세 확정 취소. 이유 필수 | **사람** |
| `GET /api/finance/statements?period=&date=&format=` | ① 손익계산서, `csv`면 내려받기 | 없음 |
| `GET /api/finance/reconciliation?date=` | ② 결제별 PG 대사 (생략 = 최근 7일) | 없음 |
| `POST /api/finance/reconciliation/{payment_id}/note` | ② 불일치 처리 메모 | **사람** |
| `POST /api/finance/pg/file?name=` | ② PG 정산 파일(CSV) 올리기. 그 기간은 파일과 대사 | **사람** |
| `GET /api/finance/pg/imports` | ② 올린 PG 정산 파일 기록·기간 | 없음 |
| `POST /api/finance/planning-requests` | 재무 개선 요청을 기획(`create_ticket`)에 보냄 | **사람** |
| `GET /api/finance/planning-requests` | 기획에 보낸 개선 요청 기록 (거둬들인 요청은 뺌) | 없음 |
| `POST /api/finance/planning-requests/{source_ref}/withdraw` | 보낸 요청 거둬들이기 | **사람** |
| `GET /api/finance/planning-questions` | 기획이 되물었는데 재무 기록으로 못 답한 질문 | 없음 |
| `POST /api/finance/planning-questions/{question_id}/answer` | 그 질문에 답함 | **사람** |
| `POST /api/finance/pg/clear` | ② 올린 PG 정산 자료 모두 빼기(이유 필수, 기록은 남김) | **사람** |
| `GET /api/finance/settlements?period=YYYY-Www` | ③ 판매자 정산 내역서 (고객지원 것) | 없음 |
| `GET /api/finance/settlements/deadlines` | ③ 지급 기한 점검 (고객지원 것) | 없음 |
| `GET /api/finance/payables?date=` | ③ 판매자 미지급금 롤포워드 (그 달) | 없음 |
| `GET /api/finance/variance?date=` | ① 월간 변동 분석 (지난달·작년 같은 달 대비) | 없음 |
| `GET /api/finance/bank` | ② 은행 입금 대조 (올린 거래내역 기간만) | 없음 |
| `POST /api/finance/bank/statement` | ② 은행 거래내역 올리기 (출금은 음수) | **사람** |
| `POST /api/finance/bank/file?name=` | ② 은행 거래내역 파일(CSV·xlsx)을 서버가 읽기 | **사람** |
| `POST /api/finance/bank/imports/{import_id}/remove` | ② 은행 내역 하나 빼기 | **사람** |
| `GET /api/finance/bank/outflows` | ② 은행 출금 대조 (판매자 이체·항목별·설명 없는 출금) | 없음 |
| `POST /api/finance/bank/outflows/{row_id}/note` | ② 설명 없는 출금 메모 | **사람** |
| `GET /api/finance/cashflow` | ③ 13주 현금 흐름 (확정 + 추정, 통장 잔액 기초) | 없음 |
| `GET /api/finance/close-checklist?date=` | ① 월 마감 체크리스트 | 없음 |
| `GET /api/finance/tax/vat?date=` | ⑤ 부가세 신고 보조 집계 (분기) | 없음 |
| `GET /api/finance/tax/seller-report?date=&format=` | ⑤ 판매자별 판매 명세 (제75조 초안) | 없음 |
| `GET /api/finance/team-budget?month=YYYY-MM` | ④ 팀 예산 (팀 인원 × 인당 월 단가) | 없음 |
| `POST /api/finance/team-budget/{month}/approve` | 쓰지 않음(403). 확정은 인사, 재무는 기록 | - |
| `GET /api/finance/rules` | 재무·세무 규칙과 근거 | 없음 |
| `GET /api/finance/other-pnl?month=` | ① 그 달의 장부 밖 손익 | 없음 |
| `POST /api/finance/other-pnl` | ① 장부 밖 손익 입력. 확정한 달은 409 | **사람** |
| `GET /api/finance/campaigns` | ⑥ 캠페인 손익·예산 판단·교훈·의견 | 없음 |
| `GET /api/finance/campaigns/compare?channel=&requested=` | ⑥ 실패 이력 전/후 판단 비교 | 없음 |
| `GET /api/finance/insights` | ⑥ 재무 인사이트와 근거 | 없음 |
| `GET /api/finance/tax/income-estimate?year=` | ⑤ 법인세 참고 추정 | 없음 |
| `GET /api/finance/exports` | ⑤ 자동으로 만든 파일과 은행 폴더 기록 | 없음 |
| `GET /api/finance/exports/{id}/files/{name}` | ⑤ 파일 내려받기 | 없음 |
| `GET /api/finance/exports/{id}/bundle` | ⑤ 묶음 전체 zip 내려받기 | 없음 |
| `POST /api/finance/exports/run` | ⑤ 지금 만들기 | **사람** |

`meta`: `mock`, `collected_at`, `source`(`seed`|`shop-api`), `unavailable`(못 받은 자료 이름).
사람 결정 기록은 메모리(데모용)다.

판매자 정산(정산금 계산 · 지급 승인 요청 · 묶음 상태 · 지급 기한)은 고객지원이 한다(2026-11-12).
재무는 정산금을 계산하지 않고 지급 승인도 요청하지 않는다(`execute("confirm_settlement")`를
부르지 않는다). 정산 두 조회는 고객지원 skill(`seller_settlement`·`seller_settlement_deadlines`)
결과를 그대로 넘기고, 못 받으면 503 `support_unavailable`이다. 미지급금·현금 흐름·출금 대조·
마감 점검은 받은 정산을 회계에만 쓴다(`settlement_feed.py`).
"""

from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from typing import Any

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response

from ax_agent_template import call_agent
from ax_agent_template.config import is_mock
from ax_agent_template.execute import execute
from ax_contracts.schemas import (
    TENANT_ID_DEFAULT,
    AgentRequest,
    Evidence,
    RequestMeta,
    TaskStatus,
)
from ax_contracts.schemas.planning import (
    AnswerPlanningQuestionInput,
    CreateTicketOutput,
    WithdrawTicketOutput,
)
from finance_agent import (
    bank,
    bank_file,
    cashflow,
    close_checklist,
    closing,
    exports,
    payables,
    pg_file,
    reconciliation,
    settlement_feed,
    statements,
    tax,
    team_budget,
    variance,
)
from finance_agent import campaign as cp
from finance_agent import finance_config as fc
from finance_agent.books import off
from finance_agent.handlers import (
    cash_net_krw,
    closing_key,
    current_insights,
    monthly_budget,
    started_campaigns,
    state,
    team_budget_data,
)
from finance_agent.human import human_from_authorization, label_fields
from finance_agent.planning_questions import STORE, human_answer_request, record_sent_request
from finance_agent.rules import load_rules

TODO_ITEMS = 10  # 할 일 카드에 바로 보여 줄 건수
RECON_KO = {
    "mismatch": "결제 금액과 PG 금액이 다름",
    "missing_in_pg": "PG 정산에 없음",
    "missing_in_ours": "PG에는 있는데 우리 기록 없음(환불·확인 필요)",
    "fee_anomaly": "PG 수수료가 기준과 다름",
}


OTHER_PNL_FIELDS = ("salary", "rent", "other_sga", "non_operating_income", "non_operating_expense")


def _month_confirmed(closings: dict[tuple[str, str], dict[str, Any]], month: str) -> bool:
    return ("month", f"{month}-01") in closings


def ok(data: Any, meta: dict[str, Any]) -> dict[str, Any]:
    return {"data": data, "meta": meta}


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _with_label(report: dict[str, Any], confirmation: dict[str, Any] | None) -> dict[str, Any]:
    """결산 보고에 누가 확정했나 표시(`confirmed_by_label`)를 더한다. 보고 계약은 그대로 둔다."""
    return report | {"confirmed_by_label": (confirmation or {}).get("by_label")}


async def _body(request: Request) -> dict[str, Any]:
    try:
        raw = await request.json()
    except ValueError:
        return {}
    return raw if isinstance(raw, dict) else {}


# ---------------------------------------------------------------- 파일 자동화
_export_lock = asyncio.Lock()


async def run_export_cycle(trigger: str = "auto") -> dict[str, Any]:
    """은행 폴더 읽기 → 지난달 묶음 만들기. 돈과 외부 전송은 없다.

    자동 회차와 사람의 "지금 만들기"가 겹쳐도 한 번에 하나만 돈다.
    """
    async with _export_lock:
        records = state().records
        imported = exports.scan_inbox(
            records.bank_rows, records.bank_imports, records.bank_out_periods
        )
        pg_imported = scan_pg_inbox(records)
        if pg_imported:
            from finance_agent import screen_cache

            screen_cache.CACHE.clear()  # 대사 화면을 새 PG 자료로 다시 계산한다
        books = await state().fresh_books()
        package = exports.make_package(
            books,
            records.exports,
            closings=records.closings,
            filings=records.vat_filings,
            other_pnl=records.other_pnl,
        )
        return {
            "trigger": trigger,
            "package": package,
            "imported": imported,
            "pg_imported": pg_imported,
        }


def import_pg(
    records: Any,
    body: bytes,
    name: str,
    source: str,
    by: str | None,
    by_label: dict[str, str] | None = None,
) -> dict:
    """PG 정산 파일 하나를 기록에 더한다(화면 올리기·폴더 같이). 실패도 기록에 남긴다.

    `by_label`: 공용 토큰일 때 누가 했나 표시(`human.label_fields`)."""
    record: dict[str, Any] = {"file": (name.strip() or "올린 파일")[:120], "at": fc.iso(fc.now())}
    record |= {"source": source} | ({"by": by} if by else {}) | (by_label or {})
    try:
        record |= pg_file.import_file(body, name, records.pg_rows, records.pg_periods)
        records.pg_version += 1
    except pg_file.PgFileError as exc:
        record |= {"status": "failed", "reason": str(exc)[:200]}
    records.pg_imports.append(record)
    del records.pg_imports[:-100]
    return record


def scan_pg_inbox(records: Any) -> list[dict[str, Any]]:
    """`FINANCE_PG_INBOX`의 CSV를 읽어 더하고 `processed/`·`failed/`로 옮긴다."""
    folder = pg_file.inbox_dir()
    if folder is None or not folder.is_dir():
        return []
    done: list[dict[str, Any]] = []
    for path in sorted(
        p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in pg_file.INBOX_SUFFIXES
    ):
        try:
            body = path.read_bytes()
        except OSError as exc:
            body, reason = b"", str(exc)[:200]
        else:
            reason = ""
        record = (
            import_pg(records, body, path.name, "folder", None)
            if body
            else {"file": path.name, "status": "failed", "reason": reason or "빈 파일"}
        )
        dest = folder / ("processed" if record.get("status") == "imported" else "failed")
        dest.mkdir(exist_ok=True)
        path.replace(dest / f"{fc.now().strftime('%Y%m%dT%H%M%S')}-{path.name}")
        done.append(record)
    return done


# ---------------------------------------------------------------- 고객지원 정산 받기
SUPPORT_DOWN = "고객지원에서 판매자 정산을 받지 못했다 (재무는 정산을 계산하지 않는다)"
SETTLEMENT_PREPARING = (
    "고객지원이 판매자 정산 장부를 준비 중이다 (서버를 켠 뒤 처음 읽는 중). 잠시 뒤 다시 본다"
)


def support_meta(out: dict[str, Any]) -> dict[str, Any]:
    """고객지원 응답의 `meta`를 재무 응답 `meta` 모양에 얹는다. 어디서 받았는지 `provider`."""
    got = out.get("meta") if isinstance(out.get("meta"), dict) else {}
    base = {
        "mock": is_mock("finance"),
        "collected_at": fc.iso(fc.now()),
        "source": "support",
        "unavailable": [],
    }
    return base | got | {"provider": "support"}


def without_meta(out: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in out.items() if k != "meta"}


async def cashflow_view(books: Any) -> dict[str, Any]:
    """13주 현금 흐름. 판매자 정산 내역서는 고객지원에서 미리 받아 넘긴다."""
    records = state().records
    weeks = await settlement_feed.weeks(cashflow.settlement_periods(fc.today()))
    return await off(
        cashflow.forecast,
        books,
        weeks,
        opening=bank.latest_balance(records.bank_rows),
        fixed=cashflow.fixed_costs(records.other_pnl),
    )


async def checklist_view(books: Any, d: Any = None) -> dict[str, Any]:
    """월 마감 체크리스트. 정산 내역서·묶음은 고객지원에서 미리 받아 넘긴다."""
    d = d or fc.today()
    weeks = await settlement_feed.weeks(close_checklist.settlement_periods(d))
    batches = await settlement_feed.batches()
    return await off(
        close_checklist.checklist,
        books,
        state().records,
        d,
        settlements=weeks,
        batches=batches,
    )


async def outflows_view(books: Any) -> dict[str, Any]:
    """은행 출금 대조. 판매자 이체는 고객지원에서 받은 정산 묶음과 맞춘다."""
    records = state().records
    return bank.outflows(
        records.bank_rows,
        records.bank_out_periods,
        await settlement_feed.batches(),
        seller_name=books.seller_name,
        other_pnl=records.other_pnl,
        campaigns=records.campaigns,
        notes=records.bank_notes,
    )


# ---------------------------------------------------------------- 사람이 할 일
async def overview_data() -> tuple[dict[str, Any], dict[str, Any]]:
    books = await state().books()
    today = fc.today()
    totals = await off(
        closing.compute_totals, books, fc.day_start(today), fc.day_start(today + timedelta(1))
    )
    unknown = await off(closing.unknown_codes, books)
    todo: list[dict[str, Any]] = []

    # 판매자 정산은 고객지원이 한다. 재무 할 일에는 지급 승인 요청·승인이 없고, 기한을 넘길 판매자가
    # 있으면 알려 주기만 한다(처리는 고객지원 화면). 못 받으면 할 일을 지어내지 않고 이름만 남긴다
    missing: list[str] = []
    report = await settlement_feed.deadlines()
    late = settlement_feed.needs_attention(report) if report is not None else []
    if report is None:
        missing.append("seller_settlement_deadlines")
    if late:
        worst = late[0]
        todo.append(
            {
                "kind": "settlement_deadline",
                "title": (
                    f"정산 기한 확인 필요 판매자 {len(late)}곳 ({worst.get('status_label')} 포함)."
                    " 처리는 고객지원"
                ),
                "count": len(late),
                "amount": sum(r.get("amount") or 0 for r in late),
                "link": "settlements",
                # 알림만 한다. 보류 해소·지급 승인 요청은 고객지원이 한다 (재무 탭은 읽기 전용)
                "action": {"type": "open_tab"},
                "items": [
                    {
                        "ref": r.get("seller_id"),
                        "title": f"{r.get('name')} · {r.get('status_label')}",
                        "detail": f"기한 {r.get('deadline')} (D{r.get('days_left') or 0:+d})",
                        "amount": r.get("amount"),
                    }
                    for r in late[:TODO_ITEMS]
                ],
            }
        )

    for filing in state().vat_drifts:
        payable = next((x["diff"] for x in filing["lines"] if x["code"] == "payable"), None)
        todo.append(
            {
                "kind": "vat_drift",
                "title": f"{filing['quarter']} 부가세 확정 뒤 숫자가 달라짐. 세무사 확인 필요",
                "count": 1,
                "amount": payable,
                "link": "tax",
            }
        )
    drifts = state().closing_drifts
    if drifts:
        todo.append(
            {
                "kind": "closing_drift",
                "title": f"확정 뒤 숫자가 달라진 결산 {len(drifts)}건. 조정 처리 필요",
                "count": len(drifts),
                "amount": sum(x["net_diff"] for x in drifts),
                "link": "closing",
                "items": [
                    {
                        "ref": f"{x['period']}:{x['date']}",
                        "title": x["date"],
                        "detail": ", ".join(
                            f"{line['label']} {fc.won(line['diff'])}" for line in x["lines"][:3]
                        ),
                        "amount": x["net_diff"],
                    }
                    for x in drifts[:TODO_ITEMS]
                ],
            }
        )
    yesterday = today - timedelta(days=1)
    if closing_key("day", yesterday) not in state().records.closings:
        y = await off(closing.compute_totals, books, fc.day_start(yesterday), fc.day_start(today))
        todo.append(
            {
                "kind": "closing_confirm",
                "title": f"어제({yesterday.isoformat()}) 결산 확정 대기",
                "count": 1,
                "amount": None if "net_result" in unknown else y.net_result,
                "link": "closing",
                "action": {
                    "type": "closing_confirm",
                    "period": "day",
                    "date": yesterday.isoformat(),
                },
            }
        )

    recon = await off(reconciliation.reconcile, books, None, state().records.recon_notes, today)
    open_items = [i for i in reconciliation.exceptions(recon) if not i["note"]]
    if open_items:
        todo.append(
            {
                "kind": "recon_exception",
                "title": f"최근 7일 매출·PG 대사 불일치 {len(open_items)}건",
                "count": len(open_items),
                "amount": sum(reconciliation.exception_amount(i) for i in open_items),
                "link": "reconciliation",
                # 사람이 건마다 확인 결과(메모)를 남긴다
                "action": {"type": "recon_note"},
                "items": [
                    {
                        "ref": i["payment_id"],
                        "title": RECON_KO.get(i["status"], i["status"]),
                        "detail": f"주문 {i.get('order_id')}",
                        "amount": reconciliation.exception_amount(i),
                    }
                    for i in open_items[:TODO_ITEMS]
                ],
            }
        )

    # 팀 예산 확정은 인사 AI(사람)가 한다(11/13). 재무 "할 일"에 올리지 않고 ④ 탭에서 상태만 본다

    # 기획이 되물었는데 재무 AI가 기록으로 답하지 못한 질문 (사람이 답해야 기획서가 나아간다, 11/13)
    waiting = [
        q for q in state().records.planning_questions.values() if q.get("status") == "waiting"
    ]
    if waiting:
        todo.append(
            {
                "kind": "planning_question",
                "title": f"기획이 되물은 질문 {len(waiting)}건에 답하기",
                "count": len(waiting),
                "amount": None,
                "link": "planning-requests",
                "action": {"type": "planning_answer"},
                "items": [
                    {
                        "ref": q["question_id"],
                        "title": q["request_id"],
                        "detail": q.get("question") or "",
                        "amount": None,
                    }
                    for q in waiting[:TODO_ITEMS]
                ],
            }
        )
    meta = books.meta()
    if missing:
        meta = meta | {"unavailable": [*meta.get("unavailable", []), *missing]}
    return {"date": today.isoformat(), "kpis": totals.as_kpis(unknown), "todo": todo}, meta


def _team_budget_meta(missing: list[str]) -> dict[str, Any]:
    return {
        "mock": is_mock("finance"),
        "collected_at": fc.iso(fc.now()),
        "source": "hr.team_headcount",
        "unavailable": missing,
    }


# ---------------------------------------------------------------- 라우트
RECON_LIMIT = 1000
RECON_LIMIT_MAX = 5000


def add_finance_api(app: FastAPI) -> None:
    @app.get("/api/finance/overview")
    async def overview() -> dict:
        data, meta = await overview_data()
        return ok(data, meta)

    @app.get("/api/finance/closing", response_model=None)
    async def get_closing(period: str = "day", date: str | None = None) -> dict | JSONResponse:
        if period not in ("day", "week", "month"):
            return error(400, "invalid_request", "period는 day, week, month 중 하나")
        try:
            d = fc.parse_date(date) or fc.today()
        except ValueError as exc:
            return error(400, "invalid_request", str(exc))
        books = await state().books()
        confirmation = state().records.closings.get(closing_key(period, d))
        report = await closing.closing_report(  # type: ignore[arg-type]
            books, period, d, confirmation, state().records.closings
        )
        return ok(_with_label(report, confirmation), books.meta())

    @app.get("/api/finance/statements", response_model=None)
    async def get_statements(
        period: str = "month", date: str | None = None, format: str = "json"
    ) -> dict | Response:
        if period not in statements.PERIODS:
            return error(400, "invalid_request", "period는 month, quarter, year 중 하나")
        if format not in ("json", "csv", "xlsx"):
            return error(400, "invalid_request", "format은 json, csv, xlsx 중 하나")
        try:
            d = fc.parse_date(date) or fc.today()
        except ValueError as exc:
            return error(400, "invalid_request", str(exc))
        books = await state().books()
        records = state().records
        report = await off(
            statements.income_statement,
            books,
            period,
            d,
            records.closings,
            records.other_pnl,
        )
        meta = books.meta()
        if format == "json":
            return ok(report, meta)
        if format == "xlsx":
            name = statements.filename(report, "xlsx")
            return Response(
                content=statements.to_xlsx(report, meta["unavailable"]),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": f'attachment; filename="{name}"'},
            )
        return Response(
            content=statements.to_csv(report, meta["unavailable"]).encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{statements.filename(report)}"'
            },
        )

    @app.post("/api/finance/closing/confirm", response_model=None)
    async def confirm_closing(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict | JSONResponse:
        try:
            person = human_from_authorization(authorization)
        except PermissionError as exc:
            return error(401, "unauthorized", str(exc))
        body = await _body(request)
        period = body.get("period", "day")
        if period not in ("day", "week", "month"):
            return error(400, "invalid_request", "period는 day, week, month 중 하나")
        # 날짜를 빠뜨려 오늘 결산이 확정되는 일을 막는다
        if not body.get("date"):
            return error(400, "invalid_request", "date가 필요하다")
        try:
            d = fc.parse_date(body.get("date")) or fc.today()
        except (ValueError, TypeError) as exc:
            return error(400, "invalid_request", str(exc))
        key = closing_key(period, d)
        records = state().records.closings
        if key not in records and not closing.period_finished(period, d):
            return error(409, "period_not_closed", "아직 끝나지 않은 기간은 결산을 확정할 수 없다")
        books = await state().fresh_books()
        if key not in records and closing.unknown_codes(books):
            return error(
                409,
                "books_unavailable",
                "받지 못한 장부 자료가 있어 확인할 수 없는 숫자로는 결산을 확정하지 않는다",
            )
        if key not in records:
            start, end = closing.period_range(period, d)
            if period == "month" and (pending := closing.pending_recon(books, start, end)):
                return error(
                    409,
                    "recon_pending",
                    f"PG 정산 전 결제 {pending:,}건이 남아 월 결산을 확정할 수 없다",
                )
            draft = await closing.closing_report(books, period, d, None, records)
            entered = state().records.other_pnl.get(start.isoformat()[:7])
            column = statements.column(
                books,
                fc.day_start(start),
                fc.day_start(end),
                entered if period == "month" else None,
            )
            records[key] = {
                "by": person,
                **label_fields(person, request),
                "at": fc.iso(fc.now()),
                "snapshot": closing.snapshot_of(draft, books, column),
            }
        report = await closing.closing_report(books, period, d, records[key], records)
        return ok(_with_label(report, records[key]), books.meta())

    @app.post("/api/finance/closing/cancel", response_model=None)
    async def cancel_closing(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict | JSONResponse:
        """잘못한 확정을 취소한다. 지우지 않고 누가 왜 취소했는지 남긴다."""
        try:
            person = human_from_authorization(authorization)
        except PermissionError as exc:
            return error(401, "unauthorized", str(exc))
        body = await _body(request)
        period, reason = body.get("period"), body.get("reason")
        if period not in ("day", "week", "month") or not body.get("date"):
            return error(400, "invalid_request", "period와 date가 필요하다")
        if not isinstance(reason, str) or not reason.strip():
            return error(400, "invalid_request", "reason이 필요하다")
        try:
            d = fc.parse_date(body.get("date"))
        except (ValueError, TypeError) as exc:
            return error(400, "invalid_request", str(exc))
        key = closing_key(period, d)
        records = state().records
        confirmed = records.closings.get(key)
        if confirmed is None:
            return error(404, "not_confirmed", "확정된 결산이 아니다")
        records.closing_cancels.append(
            {
                "period": key[0],
                "date": key[1],
                "confirmed_by": confirmed["by"],
                **(
                    {"confirmed_by_label": confirmed["by_label"]}
                    if confirmed.get("by_label")
                    else {}
                ),
                "confirmed_at": confirmed["at"],
                "snapshot": confirmed.get("snapshot"),
                "canceled_by": person,
                **label_fields(person, request, "canceled_by_label"),
                "canceled_at": fc.iso(fc.now()),
                "reason": reason.strip()[:300],
            }
        )
        del records.closings[key]
        books = await state().fresh_books()
        report = await closing.closing_report(books, period, d, None)
        return ok(_with_label(report, None), books.meta())

    @app.get("/api/finance/other-pnl", response_model=None)
    def get_other_pnl(month: str) -> dict | JSONResponse:
        if not team_budget.MONTH_RE.match(month):
            return error(400, "invalid_request", "month는 YYYY-MM")
        records = state().records
        return ok(
            {
                "month": month,
                "entry": records.other_pnl.get(month),
                "locked": _month_confirmed(records.closings, month),
            },
            {"collected_at": fc.iso(fc.now())},
        )

    @app.post("/api/finance/other-pnl", response_model=None)
    async def post_other_pnl(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict | JSONResponse:
        """쇼핑몰 장부에 없는 월 손익을 사람이 입력한다. 고치면 이전 값을 기록에 남긴다."""
        try:
            person = human_from_authorization(authorization)
        except PermissionError as exc:
            return error(401, "unauthorized", str(exc))
        body = await _body(request)
        month = body.get("month")
        if not isinstance(month, str) or not team_budget.MONTH_RE.match(month):
            return error(400, "invalid_request", "month는 YYYY-MM")
        if month > fc.today().isoformat()[:7]:
            return error(400, "invalid_request", "아직 오지 않은 달은 입력할 수 없다")
        records = state().records
        if _month_confirmed(records.closings, month):
            return error(409, "month_confirmed", "확정한 달은 고칠 수 없다. 조정으로 처리해 달라")
        entry: dict[str, Any] = {}
        for field in OTHER_PNL_FIELDS:
            value = body.get(field, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return error(400, "invalid_request", f"{field}는 0 이상의 정수 원")
            entry[field] = value
        tax_value = body.get("income_tax")
        if tax_value is not None and (
            isinstance(tax_value, bool) or not isinstance(tax_value, int) or tax_value < 0
        ):
            return error(400, "invalid_request", "income_tax는 0 이상의 정수 원이거나 비운다")
        previous = records.other_pnl.get(month)
        history = list((previous or {}).get("history", []))
        if previous:
            history.append({k: v for k, v in previous.items() if k != "history"})
        records.other_pnl[month] = entry | {
            "income_tax": tax_value,
            "memo": str(body.get("memo") or "")[:300],
            "by": person,
            **label_fields(person, request),
            "at": fc.iso(fc.now()),
            "history": history,
        }
        return ok(
            {"month": month, "entry": records.other_pnl[month], "locked": False},
            {"collected_at": fc.iso(fc.now())},
        )

    @app.post("/api/finance/closing/adjust", response_model=None)
    async def adjust_closing(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict | JSONResponse:
        """확정한 달의 차이를 조정으로 반영한다. 재무 AI는 안을 만들고 반영은 사람이 한다."""
        try:
            person = human_from_authorization(authorization)
        except PermissionError as exc:
            return error(401, "unauthorized", str(exc))
        body = await _body(request)
        reason, signature = body.get("reason"), body.get("signature")
        if not body.get("date") or not isinstance(reason, str) or not reason.strip():
            return error(400, "invalid_request", "date와 reason이 필요하다")
        try:
            d = fc.parse_date(body.get("date"))
        except (ValueError, TypeError) as exc:
            return error(400, "invalid_request", str(exc))
        records = state().records.closings
        key = closing_key("month", d)
        confirmation = records.get(key)
        if confirmation is None or not confirmation.get("snapshot"):
            return error(404, "not_confirmed", "숫자를 저장해 확정한 달이 아니다")
        books = await state().fresh_books()
        report = await closing.closing_report(books, "month", d, confirmation, records)
        proposal = report["adjustment_proposal"]
        if proposal is None:
            return error(409, "no_drift", "조정할 차이가 없다")
        if signature != proposal["signature"]:
            return error(409, "drift_changed", "본 차이와 지금 차이가 다르다. 다시 확인해 달라")
        confirmation.setdefault("adjustments", []).append(
            {
                "for_month": key[1][:7],
                "posted_month": fc.today().isoformat()[:7],
                "lines": report["drift"],
                "journal": proposal["journal"],
                "by": person,
                **label_fields(person, request),
                "at": fc.iso(fc.now()),
                "reason": reason.strip()[:300],
            }
        )
        report = await closing.closing_report(books, "month", d, confirmation, records)
        return ok(_with_label(report, confirmation), books.meta())

    @app.post("/api/finance/tax/vat/confirm", response_model=None)
    async def confirm_vat(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict | JSONResponse:
        """끝난 분기의 부가세 숫자를 고정한다. 세무사에게 넘길 기준이고 신고가 아니다."""
        try:
            person = human_from_authorization(authorization)
        except PermissionError as exc:
            return error(401, "unauthorized", str(exc))
        body = await _body(request)
        if not body.get("date"):
            return error(400, "invalid_request", "date가 필요하다")
        try:
            d = fc.parse_date(body.get("date"))
        except (ValueError, TypeError) as exc:
            return error(400, "invalid_request", str(exc))
        filings = state().records.vat_filings
        key = tax.filing_key(d)
        if key not in filings:
            if not tax.quarter_finished(d):
                return error(409, "period_not_closed", "끝나지 않은 분기는 확정할 수 없다")
            books = await state().fresh_books()
            if closing.unknown_codes(books):
                return error(409, "books_unavailable", "받지 못한 장부 자료가 있다")
            start, end, _ = tax.quarter_range(d)
            if pending := closing.pending_recon(books, start, end):
                return error(
                    409, "recon_pending", f"PG 정산 전 결제 {pending:,}건이 남아 확정할 수 없다"
                )
            filings[key] = {
                "by": person,
                **label_fields(person, request),
                "at": fc.iso(fc.now()),
                "pack": tax.vat_pack(books, d),
                "seller_report": tax.seller_report(books, d),
            }
        books = await state().books()
        return ok(tax.vat_view(books, d, filings), books.meta())

    @app.post("/api/finance/tax/vat/cancel", response_model=None)
    async def cancel_vat(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict | JSONResponse:
        try:
            person = human_from_authorization(authorization)
        except PermissionError as exc:
            return error(401, "unauthorized", str(exc))
        body = await _body(request)
        reason = body.get("reason")
        if not body.get("date") or not isinstance(reason, str) or not reason.strip():
            return error(400, "invalid_request", "date와 reason이 필요하다")
        try:
            d = fc.parse_date(body.get("date"))
        except (ValueError, TypeError) as exc:
            return error(400, "invalid_request", str(exc))
        records = state().records
        filing = records.vat_filings.pop(tax.filing_key(d), None)
        if filing is None:
            return error(404, "not_confirmed", "확정한 분기가 아니다")
        records.vat_filing_cancels.append(
            filing
            | {
                "canceled_by": person,
                **label_fields(person, request, "canceled_by_label"),
                "canceled_at": fc.iso(fc.now()),
                "reason": reason.strip()[:300],
            }
        )
        books = await state().books()
        return ok(tax.vat_view(books, d, records.vat_filings), books.meta())

    @app.get("/api/finance/reconciliation", response_model=None)
    async def get_reconciliation(
        date: str | None = None, status: str | None = None, limit: int = RECON_LIMIT
    ) -> dict | JSONResponse:
        """대사 결과. `items`는 고른 상태(`status`, 쉼표로 여럿·`all`)의 건만 `limit`건까지 준다.

        기본은 사람이 볼 불일치(일치·정산 전 제외)다. 일치·정산 전까지 다 보내면 최근 7일이
        십수만 건(2026-11-06 서버 37MB)이라 화면이 켤 때만 따로 받는다.
        `counts`·`totals`는 늘 기간 전체다.
        """
        try:
            d = fc.parse_date(date)
            wanted = reconciliation.parse_statuses(status)
        except ValueError as exc:
            return error(400, "invalid_request", str(exc))
        if not 1 <= limit <= RECON_LIMIT_MAX:
            return error(400, "invalid_request", f"limit은 1~{RECON_LIMIT_MAX}")
        books = await state().books()
        result = await off(reconciliation.reconcile, books, d, state().records.recon_notes)
        cov = result["pg_coverage"]
        # 무엇과 맞췄나: 올린 PG 정산 파일 · 결제 기록으로 만든 모의 자료(토스 정산 형식) · 섞임
        if not cov["uploaded"]:
            source = "mock_toss_settlement"
        else:
            source = "pg_file+mock_toss_settlement" if cov["generated"] else "pg_file"
        meta = books.meta() | {"pg_source": source}
        return ok(reconciliation.pick(result, wanted, limit), meta)

    @app.post("/api/finance/reconciliation/{payment_id}/note", response_model=None)
    async def note_reconciliation(
        payment_id: str, request: Request, authorization: str | None = Header(default=None)
    ) -> dict | JSONResponse:
        try:
            person = human_from_authorization(authorization)
        except PermissionError as exc:
            return error(401, "unauthorized", str(exc))
        note = (await _body(request)).get("note")
        if not isinstance(note, str) or not note.strip() or len(note) > 500:
            return error(400, "invalid_request", "note는 1~500자 문자열")
        books = await state().fresh_books()
        payment = next((p for p in books.payments() if p["payment_id"] == payment_id), None)
        if payment is None:
            return error(404, "not_found", f"결제 {payment_id}이 장부에 없다")
        records = state().records
        records.recon_notes[payment_id] = note.strip()
        records.recon_note_log.append(
            {
                "payment_id": payment_id,
                "note": note.strip(),
                "by": person,
                **label_fields(person, request),
                "at": fc.iso(fc.now()),
            }
        )
        result = reconciliation.reconcile(books, payment["created_at"].date(), records.recon_notes)
        item = next(i for i in result["items"] if i["payment_id"] == payment_id)
        return ok(
            item,
            books.meta() | {"noted_by": person} | label_fields(person, request, "noted_by_label"),
        )

    @app.get("/api/finance/settlements", response_model=None)
    async def get_settlements(period: str | None = None) -> dict | JSONResponse:
        """고객지원 `seller_settlement`를 그대로 넘긴다. 재무는 다시 계산하지 않는다."""
        try:
            out = await settlement_feed.settlement(period)
        except settlement_feed.InvalidPeriod as exc:
            return error(400, "invalid_request", str(exc))
        if out is None and settlement_feed.preparing("seller_settlement"):
            return error(503, "settlement_preparing", SETTLEMENT_PREPARING)
        if out is None:
            return error(503, "support_unavailable", SUPPORT_DOWN)
        return ok(without_meta(out), support_meta(out))

    @app.get("/api/finance/support-costs")
    async def get_support_costs() -> dict:
        """고객지원이 닫은 문의의 비용 기록(`record_support_cost`가 남긴 것). 최근 기록이 먼저."""
        books = await state().books()
        rows = sorted(
            state().ledger.support_costs.values(), key=lambda r: r["recorded_at"], reverse=True
        )
        return ok(rows, books.meta())

    @app.get("/api/finance/team-budget", response_model=None)
    async def get_team_budget(month: str | None = None) -> dict | JSONResponse:
        try:
            m = team_budget.parse_month(month, fc.today())
        except ValueError as exc:
            return error(400, "invalid_request", str(exc))
        data, missing = await team_budget_data(m)
        return ok(data, _team_budget_meta(missing))

    @app.post("/api/finance/team-budget/{month}/approve", response_model=None)
    async def approve_team_budget(month: str) -> JSONResponse:
        """예전 "재무가 팀 예산 확정" 길. 확정은 인사 AI(사람)가 하고 재무는 기록만 한다(11/13).

        인사가 확정하면 재무 A2A `record_team_budget`으로 기록이 들어온다. 이 길로는 확정 안 한다.
        """
        return error(
            403,
            "decided_by_hr",
            "팀 예산 확정은 인사 AI에서 합니다. 재무는 인사가 확정한 결과를 기록만 합니다.",
        )

    @app.get("/api/finance/settlements/deadlines", response_model=None)
    async def get_deadlines() -> dict | JSONResponse:
        """고객지원 `seller_settlement_deadlines`를 그대로 넘긴다."""
        out = await settlement_feed.deadlines()
        if out is None and settlement_feed.preparing("seller_settlement_deadlines"):
            return error(503, "settlement_preparing", SETTLEMENT_PREPARING)
        if out is None:
            return error(503, "support_unavailable", SUPPORT_DOWN)
        return ok(without_meta(out), support_meta(out))

    @app.get("/api/finance/payables", response_model=None)
    async def get_payables(date: str | None = None) -> dict | JSONResponse:
        try:
            d = fc.parse_date(date) or fc.today()
        except ValueError as exc:
            return error(400, "invalid_request", str(exc))
        books = await state().books()
        batches = await settlement_feed.batches()
        return ok(await off(payables.rollforward, books, batches, d), books.meta())

    @app.get("/api/finance/variance", response_model=None)
    async def get_variance(date: str | None = None) -> dict | JSONResponse:
        try:
            d = fc.parse_date(date) or fc.today()
        except ValueError as exc:
            return error(400, "invalid_request", str(exc))
        books = await state().books()
        report = await off(variance.variance_report, books, d, state().records.closings)
        return ok(report, books.meta())

    @app.post("/api/finance/bank/statement", response_model=None)
    async def upload_bank(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict | JSONResponse:
        """사람이 은행에서 내려받은 거래내역을 올린다. 겹치는 줄은 한 번만 센다.

        출금은 음수 금액이다. `has_withdrawals`가 참이면(파일에 출금 열이 있었으면) 그 기간을
        출금 대조 기간으로 남긴다. 출금 열이 없던 내역은 출금 대조를 하지 않는다.
        """
        try:
            person = human_from_authorization(authorization)
        except PermissionError as exc:
            return error(401, "unauthorized", str(exc))
        body = await _body(request)
        rows, problems = bank.parse_rows(body.get("rows"))
        if problems and not rows:
            return error(400, "invalid_request", "; ".join(problems[:5]))
        records = state().records
        import_id = exports.new_import_id()
        rows = [r | {"import_id": import_id} for r in rows]  # 이 기록으로 더한 줄 (빼기용)
        added = bank.merge_rows(records.bank_rows, rows)
        with_out = body.get("has_withdrawals") is True
        if with_out:
            bank.add_out_period(records.bank_out_periods, rows, import_id)
        records.bank_imports.append(
            {
                "file": "직접 입력",
                "at": fc.iso(fc.now()),
                "source": "upload",
                "by": person,
                **label_fields(person, request),
                "status": "imported",
                "import_id": import_id,
                "added": added,
                "rows": len(rows),
                "withdrawals": sum(1 for r in rows if r["amount"] < 0),
                "has_withdrawals": with_out,
                "skipped": len(problems),
            }
        )
        del records.bank_imports[:-100]
        books = await state().books()
        result = bank.match(books, records.bank_rows)
        meta = books.meta() | {
            "uploaded_by": person,
            "added": added,
            "skipped": problems[:20],
            "withdrawals": sum(1 for r in rows if r["amount"] < 0),
            "has_withdrawals": with_out,
        }
        return ok(result, meta)

    @app.post("/api/finance/bank/file", response_model=None)
    async def upload_bank_file(
        request: Request, name: str = "", authorization: str | None = Header(default=None)
    ) -> dict | JSONResponse:
        """사람이 은행에서 내려받은 거래내역 파일(CSV·xlsx)을 그대로 올린다. 본문은 파일 바이트다.

        읽는 규칙은 폴더 읽기와 같다(`bank_file.py`): 글자 인코딩(UTF-8·CP949), 안내 줄 아래의
        머리줄, 은행마다 다른 칸 이름을 코드가 맞춘다. 올린 기록은 `bank_imports`에 남는다.
        """
        try:
            person = human_from_authorization(authorization)
        except PermissionError as exc:
            return error(401, "unauthorized", str(exc))
        body = await request.body()
        if not body:
            return error(400, "invalid_request", "파일 내용이 비어 있습니다.")
        records = state().records
        record: dict[str, Any] = {
            "file": (name.strip() or "올린 파일")[:120],
            "at": fc.iso(fc.now()),
            "source": "upload",
            "by": person,
            **label_fields(person, request),
        }
        try:
            record |= exports.import_statement(
                body, name, records.bank_rows, records.bank_out_periods
            )
        except bank_file.BankFileError as exc:
            record |= {"status": "failed", "reason": str(exc)[:200]}
            records.bank_imports.append(record)
            del records.bank_imports[:-100]
            return error(400, "invalid_file", str(exc))
        records.bank_imports.append(record)
        del records.bank_imports[:-100]
        books = await state().books()
        result = await off(bank.match, books, records.bank_rows)
        meta = books.meta() | {
            "uploaded_by": person,
            "import": record,
            "added": record["added"],
            "withdrawals": record["withdrawals"],
            "has_withdrawals": record["has_withdrawals"],
        }
        return ok(result, meta)

    @app.post("/api/finance/pg/file", response_model=None)
    async def upload_pg_file(
        request: Request, name: str = "", authorization: str | None = Header(default=None)
    ) -> dict | JSONResponse:
        """PG 관리자 화면에서 내려받은 정산 내역 파일(CSV)을 올린다. 본문은 파일 바이트다.

        파일이 덮는 판매일 기간의 결제는 이 파일과 대사한다(파일에 없으면 "PG에 없음").
        같은 결제 번호를 다시 올리면 새 줄로 바꾼다. 올린 기록은 `pg_imports`에 남는다.
        """
        try:
            person = human_from_authorization(authorization)
        except PermissionError as exc:
            return error(401, "unauthorized", str(exc))
        body = await request.body()
        if not body:
            return error(400, "invalid_request", "파일 내용이 비어 있습니다.")
        record = import_pg(
            state().records, body, name, "upload", person, label_fields(person, request)
        )
        if record["status"] != "imported":
            return error(400, "invalid_file", record["reason"])
        return ok(record, {"uploaded_by": person})

    @app.post("/api/finance/planning-requests", response_model=None)
    async def send_planning_request(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict | JSONResponse:
        """사람이 정한 재무 개선 요청을 기획 `create_ticket`으로 보낸다.

        안전 관문(`finance_request_change`)을 먼저 거치고, 실행되지 않으면 아무것도 보내지 않는다.
        보낸 근거(`evidence` 줄)는 기록해 두고, 기획이 되물으면(`answer_planning_question`)
        그 기록으로 답한다. 재무는 요청만 보내고 기획서를 쓰거나 우선순위를 정하지 않는다.
        """
        try:
            person = human_from_authorization(authorization)
        except PermissionError as exc:
            return error(401, "unauthorized", str(exc))
        body = await _body(request)
        title, evidence = body.get("title"), body.get("evidence")
        ids = body.get("evidence_ids") or []
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
            return error(400, "invalid_request", "title은 1~120자 문자열")
        if not isinstance(evidence, str) or not 1 <= len(evidence.strip()) <= 2000:
            return error(400, "invalid_request", "evidence는 1~2000자 문자열")
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or len(ids) > 20:
            return error(400, "invalid_request", "evidence_ids는 문자열 20개까지")
        records = state().records
        number = len(records.planning_requests) + 1
        source_ref = f"fin_req_{number:04d}"
        context_id = f"ctx_req_finance_{number:04d}"
        meta_req = RequestMeta(
            context_id=context_id,
            token="finance-dashboard",
            deadline=fc.now() + timedelta(seconds=40),
            idempotency_key=f"{context_id}:finance_request_change",
        )
        decision = await execute(
            "finance_request_change",
            {"source_ref": source_ref, "evidence_count": len(ids)},
            meta_req,
            Evidence(
                data_summary=f"재무 개선 요청: {title.strip()[:80]}",
                collected_at=fc.now(),
                ref_ids=[source_ref, *ids],
            ),
        )
        if decision.status != "executed":
            return error(409, "not_executed", f"안전 관문이 실행하지 않았다({decision.status})")
        reply = await call_agent(
            "planning",
            AgentRequest(
                meta=meta_req,
                skill="create_ticket",
                input={
                    "title": title.strip(),
                    "evidence": evidence.strip(),
                    "source_ref": source_ref,
                    "evidence_ids": ids,
                    # 사람이 한 건씩 정해 보낸 요청이라 비슷한 요청이 있어도 기획에 따로 들어간다
                    "separate": True,
                },
            ),
        )
        if reply.status != TaskStatus.COMPLETED or reply.outcome not in (
            "created",
            "linked_duplicate",
        ):
            return error(
                502,
                "planning_unavailable",
                f"기획이 요청을 받지 못했다({reply.outcome or reply.status.value})",
            )
        ticket = CreateTicketOutput.model_validate(reply.output)
        facts = [line.strip() for line in evidence.splitlines() if line.strip()]
        row = {
            "source_ref": source_ref,
            "title": title.strip(),
            "facts": facts,
            "evidence_ids": ids,
            "request_id": ticket.request_id,
            "context_id": ticket.context_id,
            "outcome": ticket.outcome,
            "duplicate_of": ticket.duplicate_of,
            "tenant_id": meta_req.tenant_id,
            "by": person,
            **label_fields(person, request),
            "at": fc.iso(fc.now()),
        }
        records.planning_requests.append(row)
        record_sent_request(
            meta_req.tenant_id, ticket.context_id, ticket.request_id, facts=facts, evidence_ids=ids
        )
        return ok(row, {"sent_by": person})

    @app.post("/api/finance/planning-requests/{source_ref}/withdraw", response_model=None)
    async def withdraw_planning_request(
        source_ref: str, request: Request, authorization: str | None = Header(default=None)
    ) -> dict | JSONResponse:
        """보낸 개선 요청을 거둬들인다. 기획 `withdraw_ticket`이 지우면 재무 목록에서도 뺀다.

        안전 관문(`finance_withdraw_change`)을 먼저 거친다. 기획에 이미 없으면(`not_found`) 재무
        기록만 뺀다. 개발로 넘어갔거나 다른 부서 요청이면 기획이 거절하고 재무 기록은 그대로 둔다.
        기록은 지우지 않고 `withdrawn_at`·`withdrawn_by`를 남긴다(번호를 다시 쓰지 않게).
        """
        try:
            person = human_from_authorization(authorization)
        except PermissionError as exc:
            return error(401, "unauthorized", str(exc))
        body = await _body(request)
        reason = body.get("reason") or ""
        if not isinstance(reason, str) or len(reason) > 200:
            return error(400, "invalid_request", "reason은 200자까지 문자열")
        records = state().records
        row = next(
            (
                r
                for r in records.planning_requests
                if r.get("source_ref") == source_ref and not r.get("withdrawn_at")
            ),
            None,
        )
        if row is None or not row.get("request_id"):
            return error(404, "not_found", f"보낸 요청 {source_ref}가 없습니다.")
        tenant = row.get("tenant_id") or TENANT_ID_DEFAULT
        stamp = fc.now().strftime("%Y%m%d%H%M%S")
        meta_req = RequestMeta(
            context_id=row.get("context_id") or f"ctx_req_{source_ref}",
            tenant_id=tenant,
            token="finance-dashboard",
            deadline=fc.now() + timedelta(seconds=40),
            idempotency_key=f"{source_ref}:withdraw_ticket:{stamp}",
        )
        decision = await execute(
            "finance_withdraw_change",
            {"source_ref": source_ref, "request_id": row["request_id"]},
            meta_req,
            Evidence(
                data_summary=f"재무 개선 요청 거둬들이기: {row['request_id']} {source_ref}",
                collected_at=fc.now(),
                ref_ids=[source_ref, row["request_id"]],
            ),
        )
        if decision.status != "executed":
            return error(409, "not_executed", f"안전 관문이 실행하지 않았다({decision.status})")
        reply = await call_agent(
            "planning",
            AgentRequest(
                meta=meta_req,
                skill="withdraw_ticket",
                input={
                    "request_id": row["request_id"],
                    "source_ref": source_ref,
                    "reason": reason.strip(),
                },
            ),
        )
        if reply.status != TaskStatus.COMPLETED:
            return error(
                502,
                "planning_unavailable",
                f"기획이 요청을 받지 못했다({reply.outcome or reply.status.value})",
            )
        out = WithdrawTicketOutput.model_validate(reply.output)
        if out.outcome not in ("withdrawn", "not_found"):
            return error(409, out.outcome, out.message or "기획이 지우지 않았습니다.")
        row["withdrawn_at"] = fc.iso(fc.now())
        row["withdrawn_by"] = person
        row.update(label_fields(person, request, "withdrawn_by_label"))
        row["withdraw_outcome"] = out.outcome
        STORE.remove(tenant, row["request_id"])
        return ok(row, {"withdrawn_by": person, "planning": out.outcome})

    @app.get("/api/finance/planning-questions")
    async def list_planning_questions() -> dict:
        """기획이 되물었는데 재무 기록으로 못 답한 질문. 답 기다리는 것이 먼저, 그다음 최근 순."""
        rows = sorted(
            state().records.planning_questions.values(),
            key=lambda q: (q.get("status") != "waiting", q.get("asked_at") or ""),
        )
        waiting = [q for q in rows if q.get("status") == "waiting"]
        answered = sorted(
            (q for q in rows if q.get("status") != "waiting"),
            key=lambda q: q.get("answered_at") or "",
            reverse=True,
        )
        return ok({"questions": waiting + answered, "waiting": len(waiting)}, {})

    @app.post("/api/finance/planning-questions/{question_id}/answer", response_model=None)
    async def answer_planning_question_by_person(
        question_id: str, request: Request, authorization: str | None = Header(default=None)
    ) -> dict | JSONResponse:
        """기획 되묻기에 사람이 답한다. 안전 관문(`finance_answer_planning`)을 거쳐 기획
        `receive_planning_answer`로 보낸다. 기획이 받으면 답한 것으로 남긴다."""
        try:
            person = human_from_authorization(authorization)
        except PermissionError as exc:
            return error(401, "unauthorized", str(exc))
        body = await _body(request)
        answer = body.get("answer")
        if not isinstance(answer, str) or not 1 <= len(answer.strip()) <= 1000:
            return error(400, "invalid_request", "answer는 1~1000자 문자열")
        q = state().records.planning_questions.get(question_id)
        if q is None:
            return error(404, "not_found", f"기획 질문 {question_id}가 없습니다.")
        if q.get("status") != "waiting":
            return error(409, "already_answered", "이미 답한 질문입니다.")
        meta_req = RequestMeta(
            context_id=q["context_id"],
            tenant_id=q.get("tenant_id") or TENANT_ID_DEFAULT,
            token="finance-dashboard",
            deadline=fc.now() + timedelta(seconds=40),
            idempotency_key=f"{q['context_id']}:finance_answer_planning:{question_id}",
        )
        decision = await execute(
            "finance_answer_planning",
            {"question_id": question_id, "request_id": q["request_id"]},
            meta_req,
            Evidence(
                data_summary=f"기획 질문 {question_id} 답 ({q['request_id']})",
                collected_at=fc.now(),
                ref_ids=[question_id, q["request_id"]],
            ),
        )
        if decision.status != "executed":
            return error(409, "not_executed", f"안전 관문이 실행하지 않았다({decision.status})")
        question = AnswerPlanningQuestionInput(
            question_id=question_id,
            request_id=q["request_id"],
            question=q.get("question") or "",
            about=q.get("about"),
            options=list(q.get("options") or []),
        )
        reply = await call_agent(
            "planning", human_answer_request(meta_req, question, answer.strip())
        )
        if reply.status != TaskStatus.COMPLETED:
            return error(
                502,
                "planning_unavailable",
                f"기획이 답을 받지 못했다({reply.outcome or reply.status.value})",
            )
        q.update(
            {
                "status": "answered",
                "answer": answer.strip(),
                "answered_by": person,
                "answered_at": fc.iso(fc.now()),
                "planning_outcome": reply.outcome,
                **label_fields(person, request, "answered_by_label"),
            }
        )
        return ok(q, {"answered_by": person})

    @app.get("/api/finance/planning-requests")
    async def list_planning_requests() -> dict:
        rows = [r for r in state().records.planning_requests if not r.get("withdrawn_at")]
        return ok({"requests": list(reversed(rows))}, {})

    @app.get("/api/finance/pg/imports")
    async def pg_imports() -> dict:
        records = state().records
        return ok(
            {
                "imports": list(reversed(records.pg_imports[-20:])),
                "periods": records.pg_periods[-20:],
                "rows": len(records.pg_rows),
                "inbox_enabled": pg_file.inbox_dir() is not None,
            },
            {},
        )

    @app.post("/api/finance/pg/clear", response_model=None)
    async def clear_pg(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict | JSONResponse:
        """올린 PG 정산 자료를 모두 뺀다(시연이 끝났거나 잘못 올린 경우). 다시 모의 자료와 맞춘다.

        이유가 꼭 있어야 하고, 뺀 사람·이유·줄 수를 `pg_imports`에 남긴다.
        """
        try:
            person = human_from_authorization(authorization)
        except PermissionError as exc:
            return error(401, "unauthorized", str(exc))
        reason = str((await _body(request)).get("reason") or "").strip()[:200]
        if not reason:
            return error(400, "invalid_request", "빼는 이유(reason)가 필요합니다.")
        records = state().records
        record = {
            "file": None,
            "at": fc.iso(fc.now()),
            "source": "clear",
            "by": person,
            **label_fields(person, request),
            "status": "cleared",
            "rows": len(records.pg_rows),
            "periods": len(records.pg_periods),
            "reason": reason,
        }
        records.pg_rows.clear()
        records.pg_periods.clear()
        records.pg_version += 1
        records.pg_imports.append(record)
        del records.pg_imports[:-100]
        return ok(record, {"cleared_by": person})

    @app.post("/api/finance/bank/imports/{import_id}/remove", response_model=None)
    async def remove_bank_import(
        import_id: str, request: Request, authorization: str | None = Header(default=None)
    ) -> dict | JSONResponse:
        """잘못(시험으로) 올린 은행 거래내역 하나를 뺀다. 그 기록으로 더한 줄·출금 대조 기간도 뺀다.

        PG `clear`와 같이 이유가 꼭 있어야 하고, 뺀 사람·시각·이유·줄 수를 `bank_imports`에 남긴다.
        원래 기록도 지우지 않고 `removed`를 붙인다.
        """
        try:
            person = human_from_authorization(authorization)
        except PermissionError as exc:
            return error(401, "unauthorized", str(exc))
        reason = str((await _body(request)).get("reason") or "").strip()[:200]
        if not reason:
            return error(400, "invalid_request", "빼는 이유(reason)가 필요합니다.")
        records = state().records
        try:
            record = exports.remove_import(
                records.bank_imports,
                records.bank_rows,
                records.bank_out_periods,
                import_id,
                person,
                reason,
                by_label=label_fields(person, request).get("by_label"),
            )
        except exports.RemoveError as exc:
            return error(404 if exc.code == "not_found" else 409, exc.code, str(exc))
        return ok(record, {"removed_by": person})

    @app.get("/api/finance/bank/outflows")
    async def get_bank_outflows() -> dict:
        books = await state().books()
        return ok(await outflows_view(books), books.meta())

    @app.post("/api/finance/bank/outflows/{row_id}/note", response_model=None)
    async def note_bank_outflow(
        row_id: str, request: Request, authorization: str | None = Header(default=None)
    ) -> dict | JSONResponse:
        """설명 없는 출금에 이유를 적는다. 장부는 바꾸지 않는다 (틀린 돈은 조정 분개로)."""
        try:
            person = human_from_authorization(authorization)
        except PermissionError as exc:
            return error(401, "unauthorized", str(exc))
        note = (await _body(request)).get("note")
        if not isinstance(note, str) or not note.strip() or len(note) > 500:
            return error(400, "invalid_request", "note는 1~500자 문자열")
        books = await state().books()
        view = await outflows_view(books)
        if row_id not in {u["id"] for u in view["unexplained"]}:
            return error(404, "not_found", "설명 없는 출금 목록에 없는 거래다")
        records = state().records
        entry = {
            "note": note.strip(),
            "by": person,
            **label_fields(person, request),
            "at": fc.iso(fc.now()),
        }
        records.bank_notes[row_id] = entry
        records.bank_note_log.append({"row_id": row_id, **entry})
        return ok(await outflows_view(books), books.meta() | {"noted_by": person})

    @app.get("/api/finance/bank")
    async def get_bank() -> dict:
        books = await state().books()
        return ok(await off(bank.match, books, state().records.bank_rows), books.meta())

    @app.get("/api/finance/cashflow")
    async def get_cashflow() -> dict:
        books = await state().books()
        return ok(await cashflow_view(books), books.meta())

    @app.get("/api/finance/close-checklist", response_model=None)
    async def get_close_checklist(date: str | None = None) -> dict | JSONResponse:
        try:
            d = fc.parse_date(date) or fc.today()
        except ValueError as exc:
            return error(400, "invalid_request", str(exc))
        books = await state().books()
        return ok(await checklist_view(books, d), books.meta())

    @app.get("/api/finance/campaigns")
    async def get_campaigns() -> dict:
        records = state().records
        budget = await monthly_budget()
        month = fc.today().isoformat()[:7]
        started = await started_campaigns()
        spent = (
            None
            if started is None
            else cp.committed_this_month("", month, records.campaigns, started)
        )
        running = [
            row
            for cid, row in (started or {}).items()
            if cid not in records.campaigns and row["active_krw"] > 0
        ]
        return ok(
            {
                "campaigns": sorted(
                    records.campaigns.values(), key=lambda c: c["period_to"], reverse=True
                ),
                "running": running,
                "reviews": list(reversed(records.budget_reviews)),
                "lessons": list(reversed(records.lessons)),
                "opinions": list(reversed(records.opinions)),
                "budget": {
                    "month": month,
                    "monthly_krw": budget["amount_krw"],
                    "provisional": budget["provisional"],
                    "basis": budget["basis"],
                    "committed_krw": spent,
                },
                "break_even_roas": cp.break_even_roas(),
            },
            {
                "collected_at": fc.iso(fc.now()),
                "source": "finance.campaigns",
                "mock": False,
                "unavailable": [],
            },
        )

    @app.get("/api/finance/campaigns/compare", response_model=None)
    async def get_campaign_compare(channel: str, requested: int) -> dict | JSONResponse:
        if requested <= 0:
            return error(400, "invalid_request", "requested는 1원 이상")
        records = state().records
        ask = {
            "campaign_id": "compare",
            "campaign_name": "비교용 요청",
            "channel": channel,
            "requested_krw": requested,
        }
        result = cp.compare_with_history(
            ask,
            campaigns=records.campaigns,
            reviews=records.budget_reviews,
            lessons=records.lessons,
            cash_net_krw=await cash_net_krw(),
            today=fc.today(),
            monthly_budget=await monthly_budget(),
            started=await started_campaigns(),
        )
        return ok(result, {"collected_at": fc.iso(fc.now())})

    @app.get("/api/finance/insights")
    async def get_insights() -> dict:
        return ok(await current_insights(), {"collected_at": fc.iso(fc.now())})

    @app.get("/api/finance/rules")
    def get_rules() -> dict:
        book = load_rules()
        return ok(book.model_dump(mode="json"), {"version": book.version, "updated": book.updated})

    @app.get("/api/finance/tax/income-estimate", response_model=None)
    async def get_income_estimate(year: int | None = None) -> dict | JSONResponse:
        if year is not None and not 2026 <= year <= fc.today().year:
            return error(400, "invalid_request", "year는 2026년 이후 올해까지만 계산한다")
        books = await state().books()
        estimate = await off(
            tax.income_tax_estimate, books, year, other_pnl=state().records.other_pnl
        )
        return ok(estimate, books.meta())

    @app.get("/api/finance/exports")
    async def get_exports() -> dict:
        records = state().records
        packages = sorted(records.exports.values(), key=lambda p: p["package_id"], reverse=True)
        return ok(
            {"packages": packages, "bank_imports": list(reversed(records.bank_imports))},
            {
                "export_enabled": exports.export_dir() is not None,
                "inbox_enabled": exports.inbox_dir() is not None,
            },
        )

    @app.get("/api/finance/exports/{package_id}/files/{name}", response_model=None)
    def get_export_file(package_id: str, name: str) -> Response | JSONResponse:
        body = exports.read_file(state().records.exports, package_id, name)
        if body is None:
            return error(404, "not_found", "파일이 없거나 내용이 기록과 다르다")
        media = {
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }.get(os.path.splitext(name)[1], "text/csv; charset=utf-8")
        return Response(
            content=body,
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.get("/api/finance/exports/{package_id}/bundle", response_model=None)
    def get_export_bundle(package_id: str) -> Response | JSONResponse:
        made = exports.bundle(state().records.exports, package_id)
        if made is None:
            return error(404, "not_found", "묶음이 없거나 파일 내용이 기록과 다르다")
        name, body = made
        return Response(
            content=body,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.post("/api/finance/exports/run", response_model=None)
    async def post_export_run(
        authorization: str | None = Header(default=None),
    ) -> dict | JSONResponse:
        try:
            person = human_from_authorization(authorization)
        except PermissionError as exc:
            return error(401, "unauthorized", str(exc))
        return ok(await run_export_cycle("manual"), {"by": person})

    @app.get("/api/finance/tax/vat", response_model=None)
    async def get_vat(date: str | None = None) -> dict | JSONResponse:
        try:
            d = fc.parse_date(date) or fc.today()
        except ValueError as exc:
            return error(400, "invalid_request", str(exc))
        books = await state().books()
        return ok(await off(tax.vat_view, books, d, state().records.vat_filings), books.meta())

    @app.get("/api/finance/tax/seller-report", response_model=None)
    async def get_seller_report(date: str | None = None, format: str = "json") -> dict | Response:
        if format not in ("json", "csv", "xlsx"):
            return error(400, "invalid_request", "format은 json, csv, xlsx 중 하나")
        try:
            d = fc.parse_date(date) or fc.today()
        except ValueError as exc:
            return error(400, "invalid_request", str(exc))
        books = await state().books()
        filing = state().records.vat_filings.get(tax.filing_key(d))
        report = filing["seller_report"] if filing else await off(tax.seller_report, books, d)
        if format == "json":
            return ok(report, books.meta())
        if format == "xlsx":
            xlsx_name = f"seller-sales-{report['from'][:7]}.xlsx"
            return Response(
                content=tax.seller_report_xlsx(report),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": f'attachment; filename="{xlsx_name}"'},
            )
        name = f"seller-sales-{report['from'][:7]}.csv"
        return Response(
            content=tax.seller_report_csv(report).encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )
