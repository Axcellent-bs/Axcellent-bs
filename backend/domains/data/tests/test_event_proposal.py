"""이벤트 제안 (`event_proposal`) · 기획에 보내기 (`event_proposal_send`).

표준 판매 저장소·제안 저장소는 `:memory:`. 네이버·기획·safety·고객지원·LLM은 모두 테스트 안의
가짜다(네트워크 없음). 숫자는 테스트 전용 가짜 주문에서 나온다.
"""

import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from ax_agent_template.llm import MockLLMClient
from ax_contracts.data_access.shop_customers import CustomerRow
from ax_contracts.data_access.shop_sales import SalesOrderLine, SalesOrderRow
from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from ax_contracts.schemas.data import (
    AttributeDef,
    CampaignResultOutput,
    CatalogSales,
    EventKind,
    EventProposalItem,
    EventProposalOutput,
    EventProposalSendOutput,
    ProposalSent,
    TrendScanInput,
)
from ax_contracts.schemas.execute import ExecuteDecision
from ax_contracts.schemas.planning import ReceiveInsightInput
from data_agent import catalog, handlers
from data_agent.clients import customer_reaction
from data_agent.connectors import ConnectorUnavailable
from data_agent.event_proposal_store import EventProposalStore, StoredProposal
from data_agent.explain import LLM_UNAVAILABLE, NUMBER_INVALID
from data_agent.sales_collect import collect_once
from data_agent.sales_fake import FAKE_DIR, FakeSalesSource, load_config
from data_agent.sales_store import SalesStore, customers_source
from data_agent.skills import event_proposal as ep
from data_agent.skills.sales_analysis import FAKE_DATA

TENANT = "shop-demo"
NOW = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)  # 한국 11/16 12:00
MONTH = "2026-12"

COUPON = EventKind(code="coupon", label="장르 쿠폰", discount_rate=0.15, conversion_low=0.03,
                   conversion_high=0.07)  # fmt: skip
SHIPPING = EventKind(code="ship", label="무료 배송", discount_rate=0.05, conversion_low=0.01,
                     conversion_high=0.03)  # fmt: skip
CFG = CatalogSales(
    min_period_orders=1,
    categories={"book": "도서"},
    genres={"g1": "장르1", "g2": "장르2"},
    genre_keywords={"g1": ["검색어1"], "g2": ["검색어2"]},
    attributes=[
        AttributeDef(key="age_band", label="연령대", values={"10s": "10대", "20s": "20대"}),
        AttributeDef(key="gender", label="성별", values={"female": "여성", "male": "남성"}),
    ],
    event_kinds=[SHIPPING, COUPON],
)


def run(coro):
    return asyncio.run(coro)


def order(n: int, buyer: str, genre: str, when: datetime, price: int = 10000) -> SalesOrderRow:
    return SalesOrderRow(
        order_id=f"o{n}", buyer_id=buyer, status="paid", created_at=when, updated_at=when,
        items_amount=price, payment_status="succeeded",
        lines=[SalesOrderLine(product_id=f"p_{genre}", category_code="book", title=f"책 {genre}",
                              qty=1, unit_price=price, genre_code=genre)],
    )  # fmt: skip


def make_store(
    now: datetime = NOW, g1: str = "g1", g2: str = "g2", small_teens: bool = False
) -> SalesStore:
    """20대 여성 10명(8명 g1), 10대 남성 10명(8명 g2). small_teens면 10대 남성 4명(기준 미만)."""
    day = now - timedelta(days=3)
    people = [(f"pa{i}", {"age_band": "20s", "gender": "female"}, g1 if i < 8 else g2)
              for i in range(10)]  # fmt: skip
    teens = 4 if small_teens else 10
    people += [(f"pb{i}", {"age_band": "10s", "gender": "male"}, g2 if i < 8 else g1)
               for i in range(teens)]  # fmt: skip
    store = SalesStore(":memory:")
    store.upsert(TENANT, [order(n, b, g, day) for n, (b, _, g) in enumerate(people)], now)
    store.finish_sync(TENANT, "shop-api", now - timedelta(minutes=1), None)
    store.upsert_customers(
        TENANT, [CustomerRow(buyer_id=b, joined_at=day, updated_at=day, attributes=a)
                 for b, a, _ in people], now,
    )  # fmt: skip
    store.finish_sync(TENANT, customers_source("shop-api"), now, None)
    return store


class FakeTrend:
    """네이버 월 단위 응답 가짜. 장르(group_name)마다 작년 같은 달 값만 바꾼다."""

    def __init__(self, same_month: dict[str, float] | None = None, error: str | None = None):
        self.same_month = same_month or {}
        self.error = error
        self.queries: list[TrendScanInput] = []

    async def search_trend(self, query: TrendScanInput) -> Any:
        self.queries.append(query)
        if self.error:
            raise ConnectorUnavailable(self.error, "가짜 오류")
        group = query.keyword_groups[0].group_name
        points, month = [], query.start_date
        while month <= query.end_date:
            points.append((month, 10.0))
            month = date(month.year + month.month // 12, month.month % 12 + 1, 1)
        points[-1] = (points[-1][0], self.same_month.get(group, 10.0))
        return SimpleNamespace(series=[SimpleNamespace(group_name=group, points=points)])


def propose(store: SalesStore, proposals: EventProposalStore | None = None, **kw: Any):
    args: dict[str, Any] = {"trend": FakeTrend({"g1": 22.0})}  # g1 작년 12월 = 평균의 2배
    args.update(kw)
    cfg = args.pop("cfg", CFG)
    max_items = args.pop("max_items", 3)
    return run(ep.propose(store, proposals or EventProposalStore(":memory:"), TENANT, MONTH,
                          max_items, cfg, "test", NOW, **args))  # fmt: skip


# ---------------------------------------------------------------- 후보·점수


def test_score_rule_and_ordering() -> None:
    out = propose(make_store()).output
    assert out.status == "ok" and len(out.proposals) == 2  # 한 장르·한 묶음은 한 번만
    first, second = out.proposals
    # 배수 1.6 (0.8 ÷ 0.5) × 시즌 2.0 × (8 ÷ 30)
    assert first.genre_code == "g1" and first.score == round(1.6 * 2.0 * 8 / 30, 4)
    assert second.genre_code == "g2" and second.score == round(1.6 * 1.0 * 8 / 30, 4)
    assert first.target_label == "20대"  # 같은 점수면 묶음 이름 순
    assert first.timing_basis.last_year_multiple == 2.0
    assert [r.kind for r in first.reasons] == ["segment", "season", "conversion"]
    assert all(r.value is not None and r.source for r in first.reasons)
    # 시즌이 반대면 순서도 반대
    flipped = propose(make_store(), trend=FakeTrend({"g2": 22.0})).output
    assert [p.genre_code for p in flipped.proposals] == ["g2", "g1"]
    assert out.summary.startswith("2026년 12월 이벤트 제안 2건")


def test_same_input_same_proposal_id() -> None:
    a = propose(make_store()).output
    b = propose(make_store()).output
    assert [p.proposal_id for p in a.proposals] == [p.proposal_id for p in b.proposals]
    pid = a.proposals[0].proposal_id
    assert pid.startswith("evp-2026-12-") and len(pid) <= 80
    other = ep.proposal_id(TENANT, "2027-01", {"age_band": "20s"}, "g1", "coupon")
    assert other != ep.proposal_id(TENANT, MONTH, {"age_band": "20s"}, "g1", "coupon")


def test_groups_below_min_size_are_never_targets() -> None:
    base = ep.read_base(make_store(small_teens=True), TENANT, CFG, "test", NOW)
    assert base.candidates
    assert all("10s" not in c.filters.values() and "male" not in c.filters.values()
               for c in base.candidates)  # fmt: skip
    assert all(c.segment_size >= 5 and c.buyers >= 5 and c.target_customers >= 5
               for c in base.candidates)  # fmt: skip


def test_assumption_flag_and_word() -> None:
    p = propose(make_store()).output.proposals[0]
    est = p.estimate
    assert est.assumption and est.conversion_source == "assumption"
    assert (p.event_kind, est.conversion_low, est.conversion_high) == ("coupon", 0.03, 0.07)
    assert "가정" in p.reasons[2].text and p.reasons[2].source.startswith("가정값")
    assert "가정" in (p.summary or "") and "(가정)" in est.formula
    assert any("가정값" in line for line in p.limits)


def test_past_campaign_range() -> None:
    past = [ep.PastCampaign("c1", 0.08), ep.PastCampaign("c2", 0.04)]
    p = propose(make_store(), past=past).output.proposals[0]
    est = p.estimate
    assert (est.conversion_low, est.conversion_high) == (0.04, 0.08)
    assert est.conversion_source == "past_campaign" and not est.assumption
    assert p.reasons[2].source == "지난 캠페인 2건 (쿠폰 사용 ÷ 발급)" and p.reasons[2].value == 6.0
    # 지난 캠페인이 있으면 전환율이 같으니 할인율이 낮은 종류가 고른다
    assert p.event_kind == "ship"


def test_past_from_coupons() -> None:
    class Conn:
        async def campaign_coupons(self, cid: str):
            if cid == "bad":
                raise ConnectorUnavailable("UNAVAILABLE", "꺼짐")
            return {"c1": [SimpleNamespace(count=100, used_units=5)],
                    "c2": [SimpleNamespace(count=0, used_units=0)]}[cid]  # fmt: skip

    got, why = run(ep.past_from_coupons(["c1", "c2"], Conn()))
    assert got == [ep.PastCampaign("c1", 0.05)] and why is None
    got, why = run(ep.past_from_coupons(["c1", "bad"], Conn()))
    assert len(got) == 1 and why == "지난 캠페인 쿠폰을 읽지 못함 [UNAVAILABLE]"


def test_estimate_and_discount_cost() -> None:
    c = ep.Candidate(filters={"age_band": "20s"}, label="20대", genre="g1", genre_label="장르1",
                     segment_size=10, buyers=8, share=0.8, base_share=0.5, lift=1.6,
                     target_customers=100, aov=10000)  # fmt: skip
    est = ep.estimate(c, COUPON, ())
    assert (est.orders_low, est.orders_high) == (3, 7)
    assert (est.revenue_low, est.revenue_high) == (30000, 70000)
    assert (est.discount_cost_low, est.discount_cost_high) == (4500, 10500)  # 매출 × 15%
    assert "100명" in est.formula and "4,500~10,500원" in est.formula


def test_season_unknown_is_an_assumed_one() -> None:
    out = propose(make_store(), trend=FakeTrend(error="UNAUTHORIZED")).output
    p = out.proposals[0]
    season = p.reasons[1]
    assert season.value == 1.0 and season.source == "가정값 (검색 트렌드 확인 불가)"
    assert "가정" in season.text and "UNAUTHORIZED" in season.text
    assert p.timing_basis.last_year_multiple is None
    assert any("1배로 가정" in line for line in p.limits)
    assert p.score == round(1.6 * 8 / 30, 4)


def test_quality_gate_and_missing_event_kinds() -> None:
    store = make_store()
    store.finish_sync(TENANT, "shop-api", NOW - timedelta(days=2), None)  # 오래된 수집
    out = propose(store).output
    assert out.status == "insufficient" and not out.proposals and out.insufficient_reasons
    none = propose(make_store(), cfg=CFG.model_copy(update={"event_kinds": []})).output
    assert none.status == "insufficient"
    assert "설정에 이벤트 종류(event_kinds)가 없음" in none.insufficient_reasons


# ---------------------------------------------------------------- LLM 문장


def test_llm_text_used_when_numbers_check_out_and_reused() -> None:
    store, proposals = make_store(), EventProposalStore(":memory:")
    first = propose(store, proposals, llm=None).output.proposals[0]
    est = first.estimate
    good = ep._Text(title="20대 장르1 쿠폰 제안",
                    summary=f"대상 {est.target_customers}명, 가정 전환율 3~7%로 예상 매출 "
                            f"{est.revenue_low:,}~{est.revenue_high:,}원입니다.")  # fmt: skip
    llm = MockLLMClient(responses={ep._Text: good})
    p = propose(store, proposals, llm=llm, max_items=1).output.proposals[0]
    assert (p.title, p.summary) == (good.title, good.summary)
    calls = len(llm.calls)
    again = propose(store, proposals, llm=llm, max_items=1).output.proposals[0]
    assert again.summary == good.summary and len(llm.calls) == calls  # 같은 숫자면 다시 안 부름


def test_llm_number_verification_failure_uses_template() -> None:
    bad = ep._Text(title="20대 장르1 쿠폰", summary="가정 전환율로 예상 매출 99,999,999원입니다.")
    run_ = propose(make_store(), llm=MockLLMClient(responses={ep._Text: bad}), max_items=1)
    p = run_.output.proposals[0]
    assert p.summary == ep.template_summary(p) and p.title == ep.template_title(p)
    assert run_.fallback and run_.fallback_reason == NUMBER_INVALID
    assert "템플릿으로 대체" in run_.evidence.data_summary
    none = propose(make_store(), llm=None, max_items=1)
    assert none.fallback_reason == LLM_UNAVAILABLE


def test_llm_must_say_assumption() -> None:
    p0 = propose(make_store(), max_items=1).output.proposals[0]
    text = ep._Text(title="20대 장르1 쿠폰", summary=f"대상 {p0.estimate.target_customers}명.")
    p = propose(make_store(), llm=MockLLMClient(responses={ep._Text: text}),
                max_items=1).output.proposals[0]  # fmt: skip
    assert p.summary == ep.template_summary(p)


# ---------------------------------------------------------------- 고객 반응


def test_high_claim_rate_is_a_limit_not_a_drop() -> None:
    row = customer_reaction._Row(product_id="p_g1", claim_count=2, defect_claim_count=1,
                                 sold_qty=20, claim_rate=0.1)  # fmt: skip
    rows = {"p_g1": row}

    async def reader(ids: list[str]) -> customer_reaction.Reactions:
        return customer_reaction.Reactions(rows=rows, asked=frozenset(ids), sales_read=True)

    out = propose(make_store(), reactions=reader).output
    g1 = next(p for p in out.proposals if p.genre_code == "g1")
    head = "고객 반응: 책 g1 최근 한 달 클레임 비율 10.0%"
    assert any(line.startswith(head) for line in g1.limits)
    g2 = next(p for p in out.proposals if p.genre_code == "g2")
    assert not any(line.startswith("고객 반응:") for line in g2.limits)

    async def broken(ids: list[str]) -> customer_reaction.Reactions:
        return customer_reaction.Reactions(reason="support.book_issue_stats 서비스가 꺼져 있음")

    p = propose(make_store(), reactions=broken).output.proposals[0]
    assert any("고객 반응(클레임 비율)을 읽지 못함" in line for line in p.limits)


# ---------------------------------------------------------------- 기획에 보내기


def _meta() -> RequestMeta:
    return RequestMeta(context_id="ctx_req_dashboard", token="mock:dashboard-api",
                       deadline=datetime.now(UTC) + timedelta(seconds=30))  # fmt: skip


async def _executed(action, params, meta, evidence):  # noqa: ANN001, ANN202
    assert action == "send_event_proposal" and meta.idempotency_key.endswith(":send")
    return ExecuteDecision(execution_id="exe_test", grade="A", status="executed")


def _planning(outcome: str = "created", status: TaskStatus = TaskStatus.COMPLETED):
    got: list[tuple[str, AgentRequest]] = []

    async def send(target: str, req: AgentRequest) -> AgentResult:
        got.append((target, req))
        done = status == TaskStatus.COMPLETED
        output = {"outcome": outcome, "proposal_id": "PROP-7"} if done else {}
        return AgentResult(meta=req.meta, status=status, outcome=outcome, output=output)

    return send, got


def _stored() -> tuple[EventProposalStore, str]:
    proposals = EventProposalStore(":memory:")
    out = propose(make_store(), proposals).output
    return proposals, out.proposals[0].proposal_id


def test_send_facts_shape() -> None:
    proposals, pid = _stored()
    stored = proposals.get(TENANT, pid)
    assert stored is not None
    insight = ep.build_insight(stored, NOW)
    ReceiveInsightInput.model_validate(insight.model_dump(mode="json"))
    assert insight.insight_id == pid and insight.skill == "event_proposal"
    assert 1 <= len(insight.facts) <= 12
    assert [f.label.split(" · ")[0] for f in insight.facts[:3]] == [
        "묶음 구매 배수 (전체 구매자 대비)",
        "시즌 배수 (작년 같은 달 ÷ 12개월 평균)",
        "전환율 가운데",
    ]
    assert insight.facts[0].value == "1.60배" and insight.facts[1].value == "2.00배"
    assert insight.facts[2].value == "5% (가정)"
    assert all(f.label and f.value and 0 < len(f.source) <= 60 for f in insight.facts)
    labels = [f.label for f in insight.facts]
    assert {"대상 고객 (최근 결제 고객)", "예상 매출", "할인 비용"} <= set(labels)
    assert insight.products == ["책 g1"] and insight.product_ids == ["p_g1"]
    assert insight.audience == "20대" and "## 계산" in (insight.body_md or "")


def test_send_creates_then_duplicate_and_records_sent() -> None:
    proposals, pid = _stored()
    send, got = _planning("created")
    sent = run(ep.send_proposal(proposals, TENANT, pid, _meta(), NOW, send=send,
                                gate=_executed))  # fmt: skip
    assert (sent.status, sent.outcome) == (TaskStatus.COMPLETED, "created")
    assert sent.output == EventProposalSendOutput(outcome="created", proposal_ref="PROP-7")
    ((target, req),) = got
    assert (target, req.skill) == ("planning", "receive_insight")  # 마케팅이 아니라 기획
    assert req.meta.idempotency_key == f"{pid}:send" and req.input["insight_id"] == pid
    assert req.meta.context_id == "ctx_req_dashboard"
    # 다시 불러오면 sent가 보인다 (숫자를 새로 써도 보낸 기록은 남는다)
    again = propose(make_store(), proposals).output.proposals[0]
    assert again.proposal_id == pid and again.sent is not None
    assert (again.sent.outcome, again.sent.proposal_ref) == ("created", "PROP-7")

    async def no_call(target: str, req: AgentRequest) -> AgentResult:
        raise AssertionError("이미 보낸 제안은 기획을 다시 부르지 않음")

    dup = run(ep.send_proposal(proposals, TENANT, pid, _meta(), NOW, send=no_call,
                               gate=_executed))  # fmt: skip
    assert (dup.outcome, dup.output.proposal_ref) == ("duplicate", "PROP-7")


def test_planning_duplicate_and_deleted_recently() -> None:
    proposals, pid = _stored()
    send, _ = _planning("duplicate")
    sent = run(ep.send_proposal(proposals, TENANT, pid, _meta(), NOW, send=send, gate=_executed))
    assert sent.output.outcome == "duplicate"
    stored = proposals.get(TENANT, pid)
    assert stored is not None and stored.item.sent is not None
    assert stored.item.sent.outcome == "duplicate"

    proposals, pid = _stored()
    send, _ = _planning("deleted_recently")
    sent = run(ep.send_proposal(proposals, TENANT, pid, _meta(), NOW, send=send, gate=_executed))
    assert sent.output.outcome == "rejected" and "최근 지운 제안" in sent.output.reason


def test_gate_rejected_does_not_send() -> None:
    proposals, pid = _stored()

    async def no_call(target: str, req: AgentRequest) -> AgentResult:
        raise AssertionError("safety가 실행하지 않으면 기획을 부르지 않음")

    async def rejected(action, params, meta, evidence):  # noqa: ANN001, ANN202
        return ExecuteDecision(execution_id="exe_x", grade="C", status="rejected")

    sent = run(ep.send_proposal(proposals, TENANT, pid, _meta(), NOW, send=no_call,
                                gate=rejected))  # fmt: skip
    assert (sent.status, sent.output.outcome) == (TaskStatus.REJECTED, "rejected")
    assert sent.output.reason == "safety가 실행하지 않음 (rejected)"
    stored = proposals.get(TENANT, pid)
    assert stored is not None and stored.item.sent is None


def test_planning_error_is_not_recorded_and_missing_proposal() -> None:
    proposals, pid = _stored()
    send, _ = _planning("http_503", TaskStatus.FAILED)
    sent = run(ep.send_proposal(proposals, TENANT, pid, _meta(), NOW, send=send, gate=_executed))
    assert (sent.status, sent.outcome) == (TaskStatus.FAILED, "http_503")
    assert sent.output.outcome == "rejected" and "http_503" in sent.output.reason
    stored = proposals.get(TENANT, pid)
    assert stored is not None and stored.item.sent is None  # 다시 누를 수 있다
    missing = run(ep.send_proposal(proposals, TENANT, "evp-none", _meta(), NOW, send=send,
                                   gate=_executed))  # fmt: skip
    assert (missing.status, missing.outcome) == (TaskStatus.FAILED, "not_found")


def test_proposals_are_tenant_scoped() -> None:
    proposals, pid = _stored()
    assert proposals.get("other-shop", pid) is None and proposals.all("other-shop") == []


# ---------------------------------------------------------------- 가짜 출처로 끝까지 · 핸들러


def test_end_to_end_with_fake_source() -> None:
    store = SalesStore(":memory:")
    src = FakeSalesSource(load_config(FAKE_DIR / "catbooks.toml"), clock=lambda: NOW)
    assert run(collect_once(src, store, source_name="fake", now=NOW)).errors == []
    sales = catalog.load_catalog("catbooks").sales
    assert sales is not None
    out = propose(store, cfg=sales, trend=FakeTrend({"essay": 13.0})).output
    assert out.status == "ok" and 1 <= len(out.proposals) <= 3
    assert out.basis is not None and out.basis.data_source == FAKE_DATA
    assert out.summary.startswith(f"[{FAKE_DATA}]")
    codes = {e.code for e in sales.event_kinds}
    for p in out.proposals:
        assert len(p.reasons) == 3 and p.event_kind in codes and p.genre_code in sales.genres
        assert p.estimate.target_customers >= sales.min_group_size and p.estimate.aov
        assert p.estimate.assumption and any(FAKE_DATA in line for line in p.limits)
        assert FAKE_DATA in p.reasons[0].source
    assert len({p.genre_code for p in out.proposals}) == len(out.proposals)
    EventProposalOutput.model_validate(out.model_dump(mode="json"))


def _request(skill: str, **inp: Any) -> AgentRequest:
    return AgentRequest(meta=_meta(), skill=skill, input=inp)


def test_handlers_propose_then_send(monkeypatch: pytest.MonkeyPatch) -> None:
    customer_reaction.clear_cache()
    now = datetime.now(UTC)
    store = make_store(now, g1="novel", g2="essay")
    proposals = EventProposalStore(":memory:")
    send, got = _planning("created")

    async def no_past() -> tuple[list[ep.PastCampaign], str | None]:
        return [], None

    async def support(target: str, req: AgentRequest) -> AgentResult:
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED,
                           output={"items": [], "sales_read": True})  # fmt: skip

    monkeypatch.setattr(handlers, "get_trend_connector", lambda: FakeTrend())
    monkeypatch.setattr(handlers, "get_llm", lambda: None)
    monkeypatch.setattr(handlers, "get_past_campaigns", no_past)
    monkeypatch.setattr(handlers, "get_reaction_sender", lambda: support)
    monkeypatch.setattr(handlers, "get_event_proposal_sender", lambda: send)
    monkeypatch.setattr(handlers, "get_event_proposal_gate", lambda: _executed)
    catalog.use_catalog(catalog.load_catalog("catbooks"))
    handlers.use_sales_store(store)
    handlers.use_event_proposal_store(proposals)
    try:
        assert {"event_proposal", "event_proposal_send"} <= set(handlers.HANDLERS)
        res = run(handlers.event_proposal_skill(_request("event_proposal", month=MONTH)))
        assert res.status == TaskStatus.COMPLETED and res.evidence
        out = EventProposalOutput.model_validate(res.output)
        assert out.status == "ok" and out.proposals
        pid = out.proposals[0].proposal_id
        sent = run(handlers.event_proposal_send(_request("event_proposal_send", proposal_id=pid)))
        bad = run(handlers.event_proposal_send(
            _request("event_proposal_send", proposal_id=pid, tenant_id="other")
        ))  # fmt: skip
    finally:
        handlers.use_sales_store(None)
        handlers.use_event_proposal_store(None)
        catalog.use_catalog(None)
        customer_reaction.clear_cache()
    assert (sent.status, sent.outcome) == (TaskStatus.COMPLETED, "created")
    assert EventProposalSendOutput.model_validate(sent.output).proposal_ref == "PROP-7"
    assert sent.evidence and "PROP-7" in sent.evidence.data_summary
    assert len(got) == 1 and bad.outcome == "invalid_input"


# ---------------------------------------------------------------- 성과 되돌리기 (예상 vs 실제)


def _past(
    item: EventProposalItem, pid: str, month: str, ref: str | None, **update: Any
) -> StoredProposal:
    """기획에 보낸 지난 제안 (같은 묶음·장르·이벤트, 달·id만 다름)."""
    sent = ProposalSent(sent_at=NOW, outcome="created", proposal_ref=ref)
    tb = item.timing_basis.model_copy(update={"month": month})
    return StoredProposal(item.model_copy(update={"proposal_id": pid, "timing_basis": tb,
                                                  "sent": sent, **update}))  # fmt: skip


def _campaign(
    cid: str, start: datetime, actual: int | None, *, ref: str | None = None,
    insight: str | None = None, test: bool = False,
) -> CampaignResultOutput:
    sales = None
    if actual is not None:
        sales = {"status": "known", "verdict": {"status": "known", "label": "더 팔림",
                                                "incremental_gross_krw": actual}}  # fmt: skip
    return CampaignResultOutput.model_validate({
        "status": "known", "campaign_id": cid, "proposal_id": ref, "insight_id": insight,
        "window_from": start.isoformat(), "window_to": (start + timedelta(days=7)).isoformat(),
        "test": test, "sales": sales,
    })  # fmt: skip


def test_past_results_link_proposal_to_campaign_by_prop_or_insight_id() -> None:
    item = propose(make_store()).output.proposals[0]
    oct_, sep, aug = (datetime(2026, m, 5, tzinfo=UTC) for m in (10, 9, 8))
    stored = [
        _past(item, "evp-old1", "2026-10", "PROP-1"),
        _past(item, "evp-old2", "2026-09", None),  # 번호 없음: insight_id(= proposal_id)로 연결
        _past(item, "evp-old3", "2026-08", "PROP-3"),  # 캠페인 없음: 예상 범위만
        _past(item, "evp-other", "2026-10", "PROP-5", genre_code="g9"),  # 다른 장르: 안 보임
    ]
    results = [
        _campaign("cmp_a", oct_, 300_000, ref="PROP-1"),
        _campaign("cmp_test", oct_, 9_999_999, ref="PROP-1", test=True),  # 시험 캠페인: 뺌
        _campaign("cmp_b", sep, -50_000, insight="evp-old2"),  # 마이너스 그대로
        _campaign("cmp_x", aug, 1, ref="PROP-9"),  # 연결 안 된 캠페인
    ]
    rows = ep.past_results(stored, item, results)
    assert [(r.proposal_id, r.proposal_ref, r.campaign_id, r.actual) for r in rows] == [
        ("evp-old1", "PROP-1", "cmp_a", 300_000),
        ("evp-old2", None, "cmp_b", -50_000),
        ("evp-old3", "PROP-3", None, None),
    ]
    est = item.estimate
    assert all((r.expected_low, r.expected_high) == (est.revenue_low, est.revenue_high)
               for r in rows)  # fmt: skip
    assert (rows[0].period_from, rows[0].period_to) == (oct_, oct_ + timedelta(days=7))
    assert rows[2].period_from is None and rows[2].period_to is None
    # 결과를 못 읽으면(빈 목록) 예상 범위만 (예전과 같은 모양)
    assert [(r.proposal_id, r.campaign_id) for r in ep.past_results(stored, item)] == [
        ("evp-old1", None), ("evp-old2", None), ("evp-old3", None)]
    # 최대 3줄: 한 제안에 캠페인이 둘이면 기간 최근 것부터 둘 다
    two = [_campaign("cmp_a2", oct_ + timedelta(days=10), None, ref="PROP-1"), *results]
    rows = ep.past_results(stored, item, two)
    assert [(r.campaign_id, r.actual) for r in rows] == [
        ("cmp_a2", None), ("cmp_a", 300_000), ("cmp_b", -50_000)]


def test_past_results_own_proposal_only_when_a_campaign_is_linked() -> None:
    item = propose(make_store()).output.proposals[0]
    me = _past(item, item.proposal_id, item.timing_basis.month, "PROP-7")
    assert ep.past_results([me], item, []) == []  # 보낸 사실은 sent에 이미 있다
    start = datetime(2026, 11, 1, tzinfo=UTC)
    (row,) = ep.past_results([me], item, [_campaign("cmp_1", start, 120_000, ref="PROP-7")])
    assert (row.proposal_id, row.campaign_id, row.actual) == (item.proposal_id, "cmp_1", 120_000)
    # 보내지 않았거나 기획이 만들지 않은(rejected) 제안은 보지 않는다
    for sent in (None, ProposalSent(sent_at=NOW, outcome="rejected", proposal_ref="PROP-7")):
        other = StoredProposal(me.item.model_copy(update={"sent": sent}))
        assert ep.past_results([other], item, [_campaign("c", start, 1, ref="PROP-7")]) == []
    assert ep.link_keys([me]) == {item.proposal_id, "PROP-7"}


def test_propose_reads_campaign_results_only_after_sending() -> None:
    proposals = EventProposalStore(":memory:")
    asked: list[set[str]] = []
    start = datetime(2026, 11, 1, tzinfo=UTC)

    async def reader(keys: set[str]) -> list[CampaignResultOutput]:
        asked.append(set(keys))
        return [_campaign("cmp_1", start, 80_000, ref="PROP-7")]

    first = propose(make_store(), proposals, campaign_results=reader)
    assert asked == [] and all(p.past_results == [] for p in first.output.proposals)
    pid = first.output.proposals[0].proposal_id
    proposals.mark_sent(TENANT, pid, ProposalSent(sent_at=NOW, outcome="created",
                                                  proposal_ref="PROP-7"))  # fmt: skip
    again = propose(make_store(), proposals, campaign_results=reader)
    assert asked == [{pid, "PROP-7"}]
    mine = next(p for p in again.output.proposals if p.proposal_id == pid)
    assert [(r.campaign_id, r.actual) for r in mine.past_results] == [("cmp_1", 80_000)]
    assert any("campaign_result" in q for q in again.evidence.queries)

    async def broken(keys: set[str]) -> list[CampaignResultOutput]:
        raise RuntimeError("영상 저장소를 못 읽음")

    failed = propose(make_store(), proposals, campaign_results=broken)
    mine = next(p for p in failed.output.proposals if p.proposal_id == pid)
    assert failed.output.status == "ok" and mine.past_results == []
    assert "지난 제안 성과(campaign_result)를 읽지 못함" in failed.evidence.data_summary
