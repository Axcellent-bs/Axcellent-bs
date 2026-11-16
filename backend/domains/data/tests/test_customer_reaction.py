"""브리프 연결 상품의 고객 반응 (고객지원 book_issue_stats → InsightEvidence.customer_reaction)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from ax_contracts.schemas.data import (
    InsightEvidence,
    InsightProduct,
    TrendInsight,
    TrendInsightBriefOutput,
)
from data_agent.clients import customer_reaction as cr
from data_agent.skills.trend_insight_brief import add_reactions

NOW = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)
META = RequestMeta(
    context_id="ctx_brief_reaction",
    token="mock:data",
    path=["data"],
    deadline=datetime.now(UTC) + timedelta(seconds=30),
)


@pytest.fixture(autouse=True)
def _fresh_cache():  # noqa: ANN202
    cr.clear_cache()
    yield
    cr.clear_cache()


def _row(pid: str, claims: int, defects: int, sold: int | None, rate: float | None) -> dict:
    return {"product_id": pid, "title": pid, "claim_count": claims, "defect_claim_count": defects,
            "return_qty": 0, "refund_cash_total": 0, "sold_qty": sold, "claim_rate": rate,
            "reason": "x"}  # fmt: skip


def _support(rows: list[dict], seen: list[AgentRequest], status=TaskStatus.COMPLETED,
             outcome: str | None = None):  # noqa: ANN202
    async def send(target: str, request: AgentRequest) -> AgentResult:
        seen.append(request)
        assert (target, request.skill) == ("support", "book_issue_stats")
        output = {"period": "month", "period_start": "2026-10-17", "period_end": "2026-11-16",
                  "items": rows, "total_claims": len(rows), "claims_without_product": 0,
                  "sales_read": True}  # fmt: skip
        return AgentResult(meta=request.meta, status=status, outcome=outcome,
                           output=output if status == TaskStatus.COMPLETED else {})  # fmt: skip

    return send


def _insight(products: list[tuple[str, str]], n: int = 1) -> TrendInsight:
    return TrendInsight(
        insight_id=f"i{n}", rank=n, topic=f"주제{n}", line="x",
        products=[InsightProduct(product_id=p, name=name, reason="r") for p, name in products],
    )  # fmt: skip


def _brief(*insights: TrendInsight) -> TrendInsightBriefOutput:
    return TrendInsightBriefOutput(
        status="known", insights=list(insights), sources=["trend_popular"]
    )


def _fn(send):  # noqa: ANN001, ANN202
    async def fn(ids: list[str]):  # noqa: ANN202
        return await cr.read(ids, META, send=send, now=NOW)

    return fn


def test_fills_reaction_per_linked_product() -> None:
    seen: list[AgentRequest] = []
    send = _support([_row("p1", 3, 1, 120, 0.025), _row("p2", 2, 0, 1, 2.0)], seen)
    out = _brief(_insight([("p1", "고양이 그림책")]), _insight([("p1", "고양이 그림책")], 2))
    queries: list[str] = []
    asyncio.run(add_reactions(out, _fn(send), queries))
    # 연결 상품이 겹쳐도 한 번만 부르고, 문의·클레임 0건 상품은 넣지 않는다
    assert len(seen) == 1
    assert seen[0].input["period"] == "month" and seen[0].input["product_ids"] == ["p1"]
    assert seen[0].input["include_inquiries"] is False
    for i in out.insights:
        (got,) = i.evidence.customer_reaction
        assert got.model_dump() == {
            "product_id": "p1", "product_name": "고양이 그림책", "period": "month",
            "claim_count": 3, "defect_count": 1, "sold_qty": 120, "claim_rate": 0.025,
            "note": None,
        }  # fmt: skip
    assert "book_issue_stats" in out.sources
    assert queries == ["book_issue_stats period=month products=1"]


def test_rate_over_one_gets_note() -> None:
    send = _support([_row("p1", 9, 2, 6, 1.5)], [])
    out = _brief(_insight([("p1", "책")]))
    asyncio.run(add_reactions(out, _fn(send), []))
    (got,) = out.insights[0].evidence.customer_reaction
    assert got.claim_rate == 1.5 and got.note == "접수 기간 기준"


def test_under_five_sold_is_left_out_with_reason() -> None:
    send = _support([_row("p1", 2, 0, 4, 0.5), _row("p2", 1, 0, None, None)], [])
    out = _brief(_insight([("p1", "적게 팔린 책"), ("p2", "판매 모름"), ("p3", "클레임 없음")]))
    asyncio.run(add_reactions(out, _fn(send), []))
    ev = out.insights[0].evidence
    assert ev.customer_reaction == []
    assert ev.missing == [
        "고객 반응 · 적게 팔린 책: 최근 한 달 판매 4권 (5권 미만이라 넣지 않음)",
        "고객 반응 · 판매 모름: 판매 수량을 읽지 못함",
        "고객 반응 · 클레임 없음: 최근 한 달 클레임이 없어 집계에 없음 (판매 수량 모름)",
    ]


@pytest.mark.parametrize(
    ("status", "outcome", "why"),
    [
        (TaskStatus.REJECTED, "service_disabled", "support.book_issue_stats 서비스가 꺼져 있음"),
        (TaskStatus.FAILED, "unreachable", "support.book_issue_stats 응답 failed (unreachable)"),
    ],
)
def test_failure_leaves_field_empty_with_reason(status, outcome, why) -> None:  # noqa: ANN001
    seen: list[AgentRequest] = []
    send = _support([_row("p1", 3, 1, 120, 0.025)], seen, status=status, outcome=outcome)
    out = _brief(_insight([("p1", "책")]))
    asyncio.run(add_reactions(out, _fn(send), []))
    ev = out.insights[0].evidence
    assert ev.customer_reaction == []
    assert ev.missing == [f"고객 반응 · 책: 읽지 못함 ({why})"]
    assert "book_issue_stats" not in out.sources
    # 실패는 캐시하지 않는다: 다음 브리프가 다시 부른다
    asyncio.run(add_reactions(_brief(_insight([("p1", "책")])), _fn(send), []))
    assert len(seen) == 2


def test_raising_reaction_fn_does_not_break_brief() -> None:
    async def boom(ids: list[str]):  # noqa: ANN202
        raise RuntimeError("x")

    out = _brief(_insight([("p1", "책")]))
    asyncio.run(add_reactions(out, boom, []))
    assert out.insights[0].evidence.missing == [
        "고객 반응 · 책: 읽지 못함 (support.book_issue_stats 호출 실패 (RuntimeError))"
    ]


def test_bad_output_shape_is_not_used() -> None:
    async def send(target: str, request: AgentRequest) -> AgentResult:
        return AgentResult(meta=request.meta, status=TaskStatus.COMPLETED,
                           output={"items": [{"product_id": "p1"}]})  # fmt: skip

    got = asyncio.run(cr.read(["p1"], META, send=send, now=NOW))
    assert got.rows == {} and got.reason == "support.book_issue_stats 응답 형식이 계약과 다름"


def test_day_cache_reuses_and_widens() -> None:
    seen: list[AgentRequest] = []
    send = _support([_row("p1", 3, 1, 120, 0.025), _row("p2", 1, 0, 50, 0.02)], seen)
    asyncio.run(cr.read(["p1"], META, send=send, now=NOW))
    asyncio.run(cr.read(["p1"], META, send=send, now=NOW + timedelta(hours=3)))
    assert len(seen) == 1  # 같은 날 같은 상품은 다시 부르지 않는다
    asyncio.run(cr.read(["p2"], META, send=send, now=NOW))
    assert len(seen) == 2 and set(seen[1].input["product_ids"]) == {"p1", "p2"}
    asyncio.run(cr.read(["p1", "p2"], META, send=send, now=NOW))
    assert len(seen) == 2
    asyncio.run(cr.read(["p1"], META, send=send, now=NOW + timedelta(days=1)))
    assert len(seen) == 3  # 한국 날짜가 바뀌면 다시


def test_no_products_no_call() -> None:
    seen: list[AgentRequest] = []
    out = _brief(_insight([]))
    asyncio.run(add_reactions(out, _fn(_support([], seen)), []))
    assert seen == [] and out.insights[0].evidence.customer_reaction == []


def test_contract_backward_compatible() -> None:
    # 칸이 없는 예전 응답도 그대로 읽히고, 기본값은 빈 목록
    old = {"youtube_videos": 3, "missing": ["네이버 없음"]}
    ev = InsightEvidence.model_validate(old)
    assert ev.customer_reaction == []
    assert "customer_reaction" in ev.model_dump()
    brief = TrendInsightBriefOutput.model_validate(
        {"status": "known", "insights": [{"insight_id": "i", "rank": 1, "topic": "t", "line": "x",
                                          "evidence": old}]}  # fmt: skip
    )
    assert brief.insights[0].evidence.customer_reaction == []
