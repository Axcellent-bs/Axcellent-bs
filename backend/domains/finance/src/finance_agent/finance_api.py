"""재무 대시보드 API (재무 재설계 6·11장). 응답은 `{data, meta}`, 실패는 `{error: {code, message}}`.

| 메서드·경로 | 하는 일 | 토큰 |
| --- | --- | --- |
| `GET /api/finance/overview` | 오늘 요약(KPI)과 사람이 할 일 | 없음 |
| `GET /api/finance/closing?period=&date=` | ① 손익·분개 미리보기·설명 | 없음 |
| `POST /api/finance/closing/confirm` | ① 결산 확정 기록 | **사람** |
| `GET /api/finance/statements?period=&date=&format=` | ① 손익계산서, `csv`면 내려받기 | 없음 |
| `GET /api/finance/reconciliation?date=` | ② 결제별 PG 대사 (생략 = 최근 7일) | 없음 |
| `POST /api/finance/reconciliation/{payment_id}/note` | ② 불일치 처리 메모 | **사람** |
| `GET /api/finance/settlements?period=YYYY-Www` | ③ 판매자 정산 내역서 (생략 = 지난주) | 없음 |
| `POST /api/finance/settlements/batch` | ③ 묶음 → `execute(confirm_settlement)` | 선택 |
| `GET /api/finance/settlements/deadlines` | ③ 판매 대금 지급 기한 점검 | 없음 |
| `GET /api/finance/payables?date=` | ③ 판매자 미지급금 롤포워드 (그 달) | 없음 |
| `GET /api/finance/variance?date=` | ① 월간 변동 분석 (지난달·작년 같은 달 대비) | 없음 |
| `GET /api/finance/bank` | ② 은행 입금 대조 (올린 거래내역 기간만) | 없음 |
| `POST /api/finance/bank/statement` | ② 은행 입금 내역 올리기 | **사람** |
| `GET /api/finance/cashflow` | ③ 13주 현금 흐름 (확정 + 추정) | 없음 |
| `GET /api/finance/close-checklist?date=` | ① 월 마감 체크리스트 | 없음 |
| `GET /api/finance/tax/vat?date=` | ⑥ 부가세 신고 보조 집계 (분기) | 없음 |
| `GET /api/finance/tax/seller-report?date=&format=` | ⑥ 판매자별 판매 명세 (제75조 초안) | 없음 |
| `GET /api/finance/policy` | ⑤ 정책표·소진율·한도 제안 | 없음 |
| `POST /api/finance/policy/{key}/ack` | ⑤ 경보 확인 메모 (이번 달 숨김) | **사람** |
| `GET /api/finance/audit?days=7&context_id=` | ⑦ 사후 감사 (사건 하나로 좁히기 선택) | 없음 |
| `GET /api/finance/anomalies` | ⑦ 처리 전 의심 건 전부(기간 제한 없음) | 없음 |
| `POST /api/finance/anomalies/{finding_ref}/resolve` | ⑦ 의심 건 처리 완료(메모) | **사람** |

`meta`: `mock`, `collected_at`, `source`(`seed`|`shop-api`), `unavailable`(못 받은 자료 이름).
정산 묶음 지급은 안전 관문이 항상 C로 보류하고 사람이 승인한다. 안전 관문에 닿지 않으면 503
`safety_unavailable`이고 아무것도 기록하지 않는다(fail-closed). 사람 결정 기록은 메모리(데모용)다.
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Any

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response

from ax_agent_template.config import agent_name_scope, agent_url, is_mock
from ax_agent_template.execute import execute
from ax_contracts.schemas import Evidence, RequestMeta
from ax_contracts.schemas.common import TENANT_ID_DEFAULT
from finance_agent import (
    anomalies,
    audit,
    bank,
    cashflow,
    close_checklist,
    closing,
    deadline,
    payables,
    policy,
    reconciliation,
    settlement,
    statements,
    tax,
    variance,
)
from finance_agent import finance_config as fc
from finance_agent.handlers import closing_key, incident_impact, note_audit, state
from finance_agent.human import human_from_authorization

TODO_ITEMS = 10  # 할 일 카드에 바로 보여 줄 건수
RECON_KO = {
    "mismatch": "결제 금액과 PG 금액이 다름",
    "missing_in_pg": "PG 정산에 없음",
    "fee_anomaly": "PG 수수료가 기준과 다름",
}
UNAVAILABLE_EXECUTION = "exe_unavailable"  # agent-template execute()가 safety에 못 닿았을 때
REVIEW_CHECK_SECONDS = 3.0


def ok(data: Any, meta: dict[str, Any]) -> dict[str, Any]:
    return {"data": data, "meta": meta}


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


async def _body(request: Request) -> dict[str, Any]:
    try:
        raw = await request.json()
    except ValueError:
        return {}
    return raw if isinstance(raw, dict) else {}


# ---------------------------------------------------------------- 정산 묶음 상태
async def refresh_batch(batch: dict[str, Any]) -> None:
    """요청 중인 묶음의 보류 건 결정을 안전 관문에서 읽어 반영한다. 못 읽으면 그대로 둔다."""
    if batch.get("status") != "requested" or not batch.get("review_id"):
        return
    if is_mock("finance") and not os.getenv("AX_SAFETY_URL"):
        return  # 목 모드에 safety가 없으면 물어볼 곳이 없다
    url = f"{agent_url('safety')}/api/safety/reviews/{batch['review_id']}"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=REVIEW_CHECK_SECONDS)
        status = response.json()["data"]["status"] if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return
    if status == "approved":
        batch["status"] = "approved"
        batch.setdefault("approved_at", fc.iso(fc.now()))
    elif status in ("rejected", "expired"):
        batch["status"] = "rejected"


def _batch_status(decision_status: str) -> str:
    return {"held": "requested", "verifying": "requested", "executed": "approved"}.get(
        decision_status, "rejected"
    )


async def settlements_view(period: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    books = await state().books()
    result = settlement.statements(books, period)
    batch = state().records.batches.get(result["period"])
    if batch:
        await refresh_batch(batch)
    unavailable = ["hold_reasons.open_claim"] if books.claims is None else []
    return settlement.apply_batch(result, batch), books.meta(unavailable)


# ---------------------------------------------------------------- 사람이 할 일
async def overview_data() -> tuple[dict[str, Any], dict[str, Any]]:
    books = await state().books()
    today = fc.today()
    totals = closing.compute_totals(books, fc.day_start(today), fc.day_start(today + timedelta(1)))
    unknown = closing.unknown_codes(books)
    todo: list[dict[str, Any]] = []

    sett, _ = await settlements_view(None)
    batch = sett["batch"]
    if batch["status"] == "requested":
        todo.append(
            {
                "kind": "settlement_batch",
                "title": f"판매자 정산 {len(batch['seller_ids'])}곳 지급 승인 대기 (안전 장치)",
                "count": len(batch["seller_ids"]),
                "amount": batch["total_payout"],
                "link": "settlements",
                # 사람이 안전 장치 보류 큐에서 진행·멈춤을 누른다
                "action": {
                    "type": "review_decision",
                    "period": sett["period"],
                    "review_id": batch["review_id"],
                },
            }
        )
    elif batch["status"] in ("none", "rejected"):
        ready = settlement.ready(sett)
        if ready:
            todo.append(
                {
                    "kind": "settlement_batch",
                    "title": f"판매자 정산 {len(ready)}곳 지급 승인 요청 대기",
                    "count": len(ready),
                    "amount": sum(r["payout"] for r in ready),
                    "link": "settlements",
                    # 사람이 묶음 지급 승인을 요청한다 (안전 장치가 C로 보류 → 승인)
                    "action": {"type": "settlement_request", "period": sett["period"]},
                }
            )

    late = deadline.needs_attention(deadline.deadline_report(books, state().records.batches, today))
    if late:
        worst = late[0]
        todo.append(
            {
                "kind": "settlement_deadline",
                "title": f"정산 기한 확인 필요 판매자 {len(late)}곳 ({worst['status_label']} 포함)",
                "count": len(late),
                "amount": sum(r["amount"] for r in late),
                "link": "settlements",
                # 보류 사유를 풀거나 이번 묶음에 넣어 기한 안에 지급되게 한다 (지급은 보류 큐 승인)
                "action": {"type": "open_tab"},
                "items": [
                    {
                        "ref": r["seller_id"],
                        "title": f"{r['name']} · {r['status_label']}",
                        "detail": f"기한 {r['deadline']} (D{r['days_left']:+d})",
                        "amount": r["amount"],
                    }
                    for r in late[:TODO_ITEMS]
                ],
            }
        )

    yesterday = today - timedelta(days=1)
    if closing_key("day", yesterday) not in state().records.closings:
        y = closing.compute_totals(books, fc.day_start(yesterday), fc.day_start(today))
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

    recon = reconciliation.reconcile(books, None, state().records.recon_notes, today)
    open_items = [i for i in reconciliation.exceptions(recon) if not i["note"]]
    if open_items:
        todo.append(
            {
                "kind": "recon_exception",
                "title": f"매출·PG 대사 불일치 {len(open_items)}건",
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

    # 사후 감사: 찾은 의심 건은 처리 전 목록에 남고, 사람이 처리 완료할 때까지 할 일로 보인다
    report, _ = await audit.audit_report(books, 7, impact_lookup=incident_impact)
    await note_audit(report["findings"], TENANT_ID_DEFAULT)
    serious = [
        e
        for e in anomalies.listing(state().records.anomalies)["open"]
        if e["severity"] in ("high", "medium")
    ]
    if serious:
        todo.append(
            {
                "kind": "audit_finding",
                "title": f"처리 전 감사 의심 {len(serious)}건",
                "count": len(serious),
                "amount": None,
                "link": "audit",
                # 사람이 조사 결과(메모)를 남기고 처리 완료한다
                "action": {"type": "anomaly_resolve"},
                "items": [
                    {
                        "ref": e["finding_ref"],
                        "title": e["title"],
                        "detail": e["detail"],
                        "amount": None,
                    }
                    for e in serious[:TODO_ITEMS]
                ],
            }
        )

    pol, _ = policy.policy_report(books)
    acks = state().records.policy_acks
    alerts = [
        r
        for r in pol["rules"]
        if r["alert"] in ("warn", "over")
        and not policy_acked(acks.get(r["key"]), r["alert"], today)
    ]
    if alerts:
        labels = ", ".join(r["label"] for r in alerts)
        todo.append(
            {
                "kind": "policy_alert",
                "title": f"예산·한도 경보 {len(alerts)}건 ({labels})",
                "count": len(alerts),
                "amount": None,
                "link": "policy",
                # 사람이 확인하고 조치를 메모로 남긴다(한도 변경은 기준표 PR). 이번 달에는 숨긴다
                "action": {"type": "policy_ack"},
                "items": [
                    {
                        "ref": r["key"],
                        "title": r["label"],
                        "detail": "한도를 넘음" if r["alert"] == "over" else "한도에 가까움",
                        "amount": None,
                    }
                    for r in alerts
                ],
            }
        )
    return {"date": today.isoformat(), "kpis": totals.as_kpis(unknown), "todo": todo}, books.meta()


ALERT_RANK = {"ok": 0, "warn": 1, "over": 2}


def policy_acked(ack: dict[str, Any] | None, alert: str, today: Any) -> bool:
    """이번 달에 같은 단계 이상을 확인했으면 할 일에서 숨긴다. 경보가 더 세지면 다시 보인다."""
    if not ack or ack.get("month") != today.isoformat()[:7]:
        return False
    return ALERT_RANK.get(alert, 9) <= ALERT_RANK.get(str(ack.get("alert")), -1)


# ---------------------------------------------------------------- 라우트
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
        report = await closing.closing_report(books, period, d, confirmation)  # type: ignore[arg-type]
        return ok(report, books.meta())

    @app.get("/api/finance/statements", response_model=None)
    async def get_statements(
        period: str = "month", date: str | None = None, format: str = "json"
    ) -> dict | Response:
        if period not in statements.PERIODS:
            return error(400, "invalid_request", "period는 month, quarter, year 중 하나")
        if format not in ("json", "csv"):
            return error(400, "invalid_request", "format은 json 또는 csv")
        try:
            d = fc.parse_date(date) or fc.today()
        except ValueError as exc:
            return error(400, "invalid_request", str(exc))
        books = await state().books()
        report = statements.income_statement(books, period, d)  # type: ignore[arg-type]
        meta = books.meta()
        if format == "json":
            return ok(report, meta)
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
        try:
            d = fc.parse_date(body.get("date")) or fc.today()
        except (ValueError, TypeError) as exc:
            return error(400, "invalid_request", str(exc))
        key = closing_key(period, d)
        records = state().records.closings
        books = await state().books()
        if key not in records and closing.unknown_codes(books):
            return error(
                409,
                "books_unavailable",
                "받지 못한 장부 자료가 있어 확인할 수 없는 숫자로는 결산을 확정하지 않는다",
            )
        records.setdefault(key, {"by": person, "at": fc.iso(fc.now())})
        report = await closing.closing_report(books, period, d, records[key])
        return ok(report, books.meta())

    @app.get("/api/finance/reconciliation", response_model=None)
    async def get_reconciliation(date: str | None = None) -> dict | JSONResponse:
        try:
            d = fc.parse_date(date)
        except ValueError as exc:
            return error(400, "invalid_request", str(exc))
        books = await state().books()
        result = reconciliation.reconcile(books, d, state().records.recon_notes)
        meta = books.meta() | {"pg_source": "mock_toss_settlement"}
        return ok(result, meta)

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
        books = await state().books()
        payment = next((p for p in books.payments() if p["payment_id"] == payment_id), None)
        if payment is None:
            return error(404, "not_found", f"결제 {payment_id}이 장부에 없다")
        records = state().records
        records.recon_notes[payment_id] = note.strip()
        records.recon_note_log.append(
            {"payment_id": payment_id, "note": note.strip(), "by": person, "at": fc.iso(fc.now())}
        )
        result = reconciliation.reconcile(books, payment["created_at"].date(), records.recon_notes)
        item = next(i for i in result["items"] if i["payment_id"] == payment_id)
        return ok(item, books.meta() | {"noted_by": person})

    @app.get("/api/finance/settlements", response_model=None)
    async def get_settlements(period: str | None = None) -> dict | JSONResponse:
        try:
            data, meta = await settlements_view(period)
        except ValueError as exc:
            return error(400, "invalid_request", str(exc))
        return ok(data, meta)

    @app.post("/api/finance/settlements/batch", response_model=None)
    async def request_batch(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict | JSONResponse:
        requested_by = None
        if authorization:  # 토큰은 선택. 보냈다면 사람 토큰이어야 한다
            try:
                requested_by = human_from_authorization(authorization)
            except PermissionError as exc:
                return error(401, "unauthorized", str(exc))
        period = (await _body(request)).get("period")
        if period is not None and not isinstance(period, str):
            return error(400, "invalid_request", "period는 YYYY-Www 문자열")
        try:
            data, meta = await settlements_view(period)
        except ValueError as exc:
            return error(400, "invalid_request", str(exc))
        name = data["period"]
        batches = state().records.batches
        current = batches.get(name)
        if current and current["status"] in ("requested", "approved"):
            return ok(data, meta)  # 같은 기간은 한 번만 요청한다
        rows = settlement.ready(data)
        if not rows:
            return error(409, "nothing_to_settle", "보류 없이 지급할 판매자가 없다")

        attempt = (current or {}).get("attempt", 0) + 1
        seller_ids = [r["seller_id"] for r in rows]
        total = sum(r["payout"] for r in rows)
        summary = f"{name} 판매자 정산 {len(rows)}곳 {fc.won(total)} 지급 승인 요청"
        params = {
            "period": name,
            "from": data["from"],
            "to": data["to"],
            "pay_date": data["pay_date"],
            "seller_ids": seller_ids,
            "total_payout": total,
            "statements": [
                {
                    k: r[k]
                    for k in (
                        "seller_id",
                        "name",
                        "orders",
                        "gross",
                        "commission",
                        "seller_coupon",
                        "refunds",
                        "payout",
                    )
                }
                for r in rows
            ],
            "summary": summary,
        }
        context_id = f"ctx_req_settlement_{name}"
        meta_req = RequestMeta(
            context_id=context_id,
            token="finance-dashboard",
            deadline=fc.now() + timedelta(seconds=40),
            idempotency_key=f"{context_id}:confirm_settlement:{name}:{attempt}",
        )
        evidence = Evidence(
            queries=["finance:settlements", "shop-api:/api/shop/orders"],
            data_summary=summary,
            collected_at=fc.now(),
            ref_ids=seller_ids,
        )
        with agent_name_scope("finance"):
            decision = await execute("confirm_settlement", params, meta_req, evidence)
        if decision.execution_id == UNAVAILABLE_EXECUTION or decision.status == "failed":
            return error(
                503,
                "safety_unavailable",
                "안전 장치에 닿지 않아 정산 묶음을 요청하지 않았다 (지급 기록 없음)",
            )
        batches[name] = {
            "status": _batch_status(decision.status),
            "review_id": decision.review_id,
            "execution_id": decision.execution_id,
            "total_payout": total,
            "seller_ids": seller_ids,
            "payouts": {r["seller_id"]: r["payout"] for r in rows},
            "attempt": attempt,
            "requested_by": requested_by,
            "requested_at": fc.iso(fc.now()),
            **({"approved_at": fc.iso(fc.now())} if decision.status == "executed" else {}),
        }
        data, meta = await settlements_view(name)
        return ok(data, meta)

    @app.get("/api/finance/support-costs")
    async def get_support_costs() -> dict:
        """고객지원이 닫은 문의의 비용 기록(`record_support_cost`가 남긴 것). 최근 기록이 먼저."""
        books = await state().books()
        rows = sorted(
            state().ledger.support_costs.values(), key=lambda r: r["recorded_at"], reverse=True
        )
        return ok(rows, books.meta())

    @app.get("/api/finance/policy")
    async def get_policy() -> dict:
        books = await state().books()
        data, missing = policy.policy_report(books)
        return ok(data, books.meta(missing))

    @app.get("/api/finance/audit", response_model=None)
    async def get_audit(days: str = "7", context_id: str | None = None) -> dict | JSONResponse:
        try:
            n = int(days)
        except ValueError:
            n = 0
        if not 1 <= n <= 90:
            return error(400, "invalid_request", "days는 1~90")
        books = await state().books()
        data, missing = await audit.audit_report(
            books, n, impact_lookup=incident_impact, context_id=context_id or None
        )
        await note_audit(data.get("findings") or [], TENANT_ID_DEFAULT)
        return ok(data, books.meta(missing))

    @app.post("/api/finance/policy/{rule_key}/ack", response_model=None)
    async def ack_policy(
        rule_key: str, request: Request, authorization: str | None = Header(default=None)
    ) -> dict | JSONResponse:
        """예산·한도 경보를 사람이 확인했다고 남긴다. 한도는 바꾸지 않는다(기준표 PR로만 바꾼다)."""
        try:
            person = human_from_authorization(authorization)
        except PermissionError as exc:
            return error(401, "unauthorized", str(exc))
        note = (await _body(request)).get("note")
        if not isinstance(note, str) or not note.strip() or len(note) > 500:
            return error(400, "invalid_request", "note는 1~500자 문자열")
        books = await state().books()
        pol, _ = policy.policy_report(books)
        rule = next((r for r in pol["rules"] if r["key"] == rule_key), None)
        if rule is None:
            return error(404, "not_found", f"규칙 {rule_key}이 정책표에 없다")
        ack = {
            "alert": rule["alert"],
            "month": fc.today().isoformat()[:7],
            "note": note.strip(),
            "by": person,
            "at": fc.iso(fc.now()),
        }
        state().records.policy_acks[rule_key] = ack
        return ok(ack | {"key": rule_key}, books.meta() | {"acked_by": person})

    @app.get("/api/finance/settlements/deadlines")
    async def get_deadlines() -> dict:
        books = await state().books()
        return ok(deadline.deadline_report(books, state().records.batches), books.meta())

    @app.get("/api/finance/payables", response_model=None)
    async def get_payables(date: str | None = None) -> dict | JSONResponse:
        try:
            d = fc.parse_date(date) or fc.today()
        except ValueError as exc:
            return error(400, "invalid_request", str(exc))
        books = await state().books()
        return ok(payables.rollforward(books, state().records.batches, d), books.meta())

    @app.get("/api/finance/variance", response_model=None)
    async def get_variance(date: str | None = None) -> dict | JSONResponse:
        try:
            d = fc.parse_date(date) or fc.today()
        except ValueError as exc:
            return error(400, "invalid_request", str(exc))
        books = await state().books()
        return ok(variance.variance_report(books, d), books.meta())

    @app.post("/api/finance/bank/statement", response_model=None)
    async def upload_bank(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict | JSONResponse:
        """사람이 은행에서 내려받은 입금 내역을 올린다. 겹치는 줄은 한 번만 센다."""
        try:
            person = human_from_authorization(authorization)
        except PermissionError as exc:
            return error(401, "unauthorized", str(exc))
        rows, problems = bank.parse_rows((await _body(request)).get("rows"))
        if problems and not rows:
            return error(400, "invalid_request", "; ".join(problems[:5]))
        added = bank.merge_rows(state().records.bank_rows, rows)
        books = await state().books()
        result = bank.match(books, state().records.bank_rows)
        meta = books.meta() | {"uploaded_by": person, "added": added, "skipped": problems[:20]}
        return ok(result, meta)

    @app.get("/api/finance/bank")
    async def get_bank() -> dict:
        books = await state().books()
        return ok(bank.match(books, state().records.bank_rows), books.meta())

    @app.get("/api/finance/cashflow")
    async def get_cashflow() -> dict:
        books = await state().books()
        return ok(cashflow.forecast(books, state().records.batches), books.meta())

    @app.get("/api/finance/close-checklist", response_model=None)
    async def get_close_checklist(date: str | None = None) -> dict | JSONResponse:
        try:
            d = fc.parse_date(date) or fc.today()
        except ValueError as exc:
            return error(400, "invalid_request", str(exc))
        books = await state().books()
        return ok(close_checklist.checklist(books, state().records, d), books.meta())

    @app.get("/api/finance/tax/vat", response_model=None)
    async def get_vat(date: str | None = None) -> dict | JSONResponse:
        try:
            d = fc.parse_date(date) or fc.today()
        except ValueError as exc:
            return error(400, "invalid_request", str(exc))
        books = await state().books()
        return ok(tax.vat_pack(books, d), books.meta())

    @app.get("/api/finance/tax/seller-report", response_model=None)
    async def get_seller_report(date: str | None = None, format: str = "json") -> dict | Response:
        if format not in ("json", "csv"):
            return error(400, "invalid_request", "format은 json 또는 csv")
        try:
            d = fc.parse_date(date) or fc.today()
        except ValueError as exc:
            return error(400, "invalid_request", str(exc))
        books = await state().books()
        report = tax.seller_report(books, d)
        if format == "json":
            return ok(report, books.meta())
        name = f"seller-sales-{report['from'][:7]}.csv"
        return Response(
            content=tax.seller_report_csv(report).encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.get("/api/finance/anomalies")
    async def get_anomalies() -> dict:
        return ok(
            anomalies.listing(state().records.anomalies),
            {"mock": is_mock("finance"), "source": "finance_records"},
        )

    @app.post("/api/finance/anomalies/{finding_ref}/resolve", response_model=None)
    async def resolve_anomaly(
        finding_ref: str, request: Request, authorization: str | None = Header(default=None)
    ) -> dict | JSONResponse:
        try:
            person = human_from_authorization(authorization)
        except PermissionError as exc:
            return error(401, "unauthorized", str(exc))
        note = (await _body(request)).get("note")
        if not isinstance(note, str) or not note.strip() or len(note) > 500:
            return error(400, "invalid_request", "note는 1~500자 문자열")
        entry = anomalies.resolve(state().records.anomalies, finding_ref, person, note.strip())
        if entry is None:
            return error(404, "not_found", f"의심 건 {finding_ref}이 목록에 없다")
        return ok(
            entry, {"mock": is_mock("finance"), "source": "finance_records", "resolved_by": person}
        )
