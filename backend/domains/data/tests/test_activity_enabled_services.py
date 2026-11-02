"""업무 리포트는 이 회사가 켠(AI를 쓰는) 서비스만 센다.

- 부서 카드는 켠 서비스의 부서만. AI를 쓰지 않는 부서로 넘긴 일은 한 줄(부서별 건수·업무 상위 3개)
- 재무 AI를 쓰지 않으면 closing_report를 부르지 않는다(꺼진 서비스로 가면 사람 할 일함에 쌓인다)
- 저장된 일간에는 그날 켠 서비스가 남아, 나중에 서비스를 더 켜도 지난 리포트가 바뀌지 않는다

켠 서비스는 백엔드 `AX_ENABLED_SERVICES` 기준이다(`routing.is_enabled`).
"""

from __future__ import annotations

import itertools
import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from ax_agent_template import config
from ax_agent_template.client import call_agent
from ax_agent_template.common_log import SqliteLogStore
from ax_agent_template.llm import MockLLMClient
from ax_contracts.data_access.common_log import LogRecord
from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, RequestMeta, TaskStatus
from ax_contracts.schemas.data import ActivityReportInput
from data_agent.clients.log_client import CommonLogSource
from data_agent.explain import _Summary
from data_agent.report_store import ReportStore
from data_agent.skills.activity_report import FINANCE_UNUSED, build_day_snapshot, build_report

asyncio = pytest.mark.asyncio
TENANT = "shop-demo"
DAY = date(2026, 10, 21)  # 수요일
LATER = datetime(2026, 11, 3, 3, 0, tzinfo=UTC)
_ids = itertools.count()


def kst(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC) - timedelta(hours=9)


def _rec(at: datetime, kind: str, source: str, **kw) -> LogRecord:
    return LogRecord(
        log_id=f"log_{next(_ids)}", tenant_id=TENANT, occurred_at=at, source=source, kind=kind, **kw
    )


def request(at: datetime, source: str, skill: str, requester: str = "orchestrator") -> list:
    rid = f"r{next(_ids)}"
    return [
        _rec(at, "request_received", source, skill=skill, requester=requester,
             context_id="ctx_req_1", payload={"skill": skill, "path": [], "request_id": rid}),
        _rec(at + timedelta(seconds=1), "result_sent", source, skill=skill, requester=requester,
             context_id="ctx_req_1", task_status="completed",
             payload={"skill": skill, "status": "completed", "outcome": None, "request_id": rid,
                      "duration_ms": 1000}),
    ]  # fmt: skip


def handed(at: datetime, caller: str, to_service: str, skill: str) -> LogRecord:
    """꺼진 서비스로 간 요청이 사람 할 일함으로 넘어간 기록 (call_agent가 남긴다)."""
    return _rec(
        at, "task_status", caller, context_id="ctx_req_h", skill=skill,
        task_status="input-required",
        payload={"skill": skill, "to_service": to_service, "outcome": "handed_to_human",
                 "task_id": f"task_{next(_ids)}"},
    )  # fmt: skip


def day_logs(day: date) -> list[LogRecord]:
    """데이터 AI가 일하고, 재무·마케팅·고객지원으로 일을 넘긴 하루."""
    logs: list[LogRecord] = []
    for n in range(3):
        logs += request(kst(day, 10, n), "data", "impact_analysis")
    logs += request(kst(day, 11), "coupon", "draft_notice", requester="data")
    logs += request(kst(day, 11, 30), "finance", "closing_report", requester="data")
    for n, skill in enumerate(["record_support_cost"] * 3 + ["seller_review"] * 2 + ["audit_report"]
                              + ["settlement_hold"]):  # fmt: skip
        logs.append(handed(kst(day, 12, n), "data", "finance", skill))
    logs.append(handed(kst(day, 13), "data", "coupon", "draft_notice"))
    logs.append(handed(kst(day, 13, 5), "data", "coupon", "draft_notice"))
    return logs


class FakeMetrics:
    async def fetch(self, metric: str, window_from: datetime, window_to: datetime):
        value = {"order_count": 10.0, "payment_count": 10.0, "payment_fail_rate": 0.1}[metric]
        return [(window_from, value)]


class FinanceSender:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, target: str, req: AgentRequest) -> AgentResult:
        self.calls.append({"target": target, **req.input})
        lines = [{"code": "gmv", "amount": 1000}, {"code": "commission_revenue", "amount": 100}]
        return AgentResult(
            meta=req.meta, status=TaskStatus.COMPLETED, output={"lines": lines},
            evidence=Evidence(collected_at=LATER),
        )  # fmt: skip


def _meta() -> RequestMeta:
    return RequestMeta(context_id="ctx_req_t", token="x", tenant_id=TENANT,
                       deadline=datetime.now(UTC) + timedelta(seconds=60))  # fmt: skip


def _logs(records: list[LogRecord]) -> CommonLogSource:
    store = SqliteLogStore(":memory:")
    for r in records:
        store.write(r)
    return CommonLogSource(store)


def only(*services: str):
    on = set(services)
    return lambda s: s in on


async def _report(logs, enabled, send=None, store=None, **inp):
    return await build_report(
        ActivityReportInput(**({"start": DAY} | inp)), _meta(),
        MockLLMClient(responses={_Summary: _Summary(summary="요약")}),
        logs=logs, metrics=FakeMetrics(), send=send or FinanceSender(), now=LATER,
        enabled=enabled, owner=lambda s: None, store=store,
    )  # fmt: skip


# ------------------------------------------------------------------------ data만 켬
@asyncio
async def test_data_only_company_sees_only_data_and_one_unused_line() -> None:
    send = FinanceSender()
    result = await _report(_logs(day_logs(DAY)), only("data"), send=send)
    out = result.output
    assert [d.department for d in out.departments] == ["data"]
    assert out.enabled_services == ["data"]
    assert _dept(out, "data").requests.total == 3
    # 재무 AI를 쓰지 않으니 재무를 부르지 않고, 매출은 0이 아니라 확인할 수 없음
    assert send.calls == []
    assert out.company.gmv.value is None
    assert {u.section: u.reason for u in out.unknown_sections}["company.gmv"] == FINANCE_UNUSED
    # AI를 쓰지 않는 부서로 넘긴 일: 부서별 건수와 업무 상위 3개 (한국어 이름)
    assert [(u.department, u.label, u.count) for u in out.unused_handoffs] == [
        ("finance", "재무", 7),
        ("marketing", "마케팅", 2),
    ]
    finance = out.unused_handoffs[0]
    assert [(s.skill, s.label, s.count) for s in finance.top_skills] == [
        ("record_support_cost", "기타 작업(record_support_cost)", 3),
        ("seller_review", "판매자 점검", 2),
        ("audit_report", "감사 보고서", 1),
    ]  # 상위 3개만 (settlement_hold 1건은 뺀다)
    # 켜지 않은 부서와의 협업·특이사항은 없다
    assert all(e.to_department == "data" for e in out.collaboration)
    assert all("finance" not in h.key and "marketing" not in h.key for h in out.highlights)
    dumped = json.dumps(out.model_dump(mode="json"), ensure_ascii=False)
    assert "도입" not in dumped and "자동 처리할 수" not in dumped  # 예측 문구를 만들지 않는다


# ------------------------------------------------------------------ 재무+마케팅만 켬
@asyncio
async def test_finance_and_marketing_only_company() -> None:
    send = FinanceSender()
    result = await _report(_logs(day_logs(DAY)), only("finance", "coupon"), send=send)
    out = result.output
    assert [d.department for d in out.departments] == ["marketing", "finance"]
    assert _dept(out, "marketing").requests.total == 1
    assert _dept(out, "finance").requests.total == 1
    assert _dept(out, "finance").handoffs.handed == 7  # 켠 부서로 넘긴 일은 그 부서 카드에
    assert out.company.gmv.value == 1000 and len(send.calls) == 2  # 재무를 쓰니 부른다
    assert out.unused_handoffs == []
    assert "data" not in [d.department for d in out.departments]


@asyncio
async def test_disabled_service_in_input_is_rejected() -> None:
    from data_agent.skills.activity_report import InvalidActivityInput

    with pytest.raises(InvalidActivityInput):
        await _report(_logs([]), only("data"), services=["finance"])


# ------------------------------------------- 재무 꺼짐: closing_report 0회, 받은 요청 0건
@asyncio
async def test_finance_off_makes_no_closing_report_and_no_handoff(monkeypatch, tmp_path) -> None:
    """실제 call_agent로 확인한다: 재무를 부르면 사람 할 일함(dashboard-api)으로 넘어가 버린다."""
    monkeypatch.setenv("AX_ENABLED_SERVICES", "data")
    org = tmp_path / "org.yaml"  # 고객사 조직도: 재무 일은 사람(재무팀)이 받는다
    org.write_text("assignments:\n  finance: {unit: 재무팀}\n", encoding="utf-8")
    monkeypatch.setenv("AX_ORG_CONFIG", str(org))
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    config.set_agent_name("data")
    sent: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(record))

    async def send(target: str, req: AgentRequest) -> AgentResult:
        return await call_agent(target, req, http_client=client)

    logs = _logs(day_logs(DAY))
    async with client:
        result = await _report(
            logs, only("data"), send=send, period="week", start=date(2026, 10, 19)
        )
        snapshot = await build_day_snapshot(
            DAY, _meta(), logs, FakeMetrics(), send, now=LATER, enabled=only("data")
        )
    assert sent == []  # closing_report도, 사람 할 일함 넣기도 없다
    # 대조: 같은 환경(조직도 있음)에서 재무를 직접 부르면 사람 할 일함(dashboard-api)으로 넘어간다
    async with httpx.AsyncClient(transport=httpx.MockTransport(record)) as again:
        req = AgentRequest(meta=_meta(), skill="closing_report", input={"period": "day"})
        handed_over = await call_agent("finance", req, http_client=again)
    assert handed_over.output.get("to_service") == "finance"
    assert [r.url.path for r in sent] == ["/api/dashboard/handoffs"]
    assert result.output.company.gmv.value is None
    assert snapshot.payload["company"]["finance_reason"] == FINANCE_UNUSED
    assert snapshot.payload["enabled_services"] == ["data"]


# ------------------------------------------- 저장된 날은 그날 켠 서비스 (지난 리포트가 안 바뀜)
@asyncio
async def test_saved_day_keeps_its_enabled_services_after_more_are_turned_on() -> None:
    logs = _logs(day_logs(DAY) + day_logs(DAY - timedelta(days=1)))
    store = ReportStore(":memory:")
    for d in (DAY - timedelta(days=1), DAY):  # 그때는 data만 썼다
        store.put(
            await build_day_snapshot(d, _meta(), logs, FakeMetrics(), FinanceSender(), now=LATER,
                                     enabled=only("data"))
        )  # fmt: skip
    before = await _report(logs, only("data"), store=store)
    after = await _report(logs, lambda s: True, store=store)  # 나중에 전부 켬
    assert (after.output.stored_days, after.output.live_days) == (2, 0)
    assert [d.department for d in after.output.departments] == ["data"]
    assert after.output.unused_handoffs == before.output.unused_handoffs
    assert after.output.enabled_services == ["data"]
    assert after.output.departments == before.output.departments


@asyncio
async def test_today_uses_services_enabled_now() -> None:
    logs = _logs(day_logs(DAY))
    result = await build_report(
        ActivityReportInput(start=DAY), _meta(),
        MockLLMClient(responses={_Summary: _Summary(summary="요약")}),
        logs=logs, metrics=FakeMetrics(), send=FinanceSender(), now=kst(DAY, 15),
        enabled=only("data", "coupon"), owner=lambda s: None, store=ReportStore(":memory:"),
    )  # fmt: skip
    assert [d.department for d in result.output.departments] == ["marketing", "data"]
    assert [u.department for u in result.output.unused_handoffs] == ["finance"]


def _dept(out, name: str):
    return next(d for d in out.departments if d.department == name)
