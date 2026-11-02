"""개발(dev) 쪽 service 시험 (분리 5단계에서 domains/planning/tests/test_service.py에서 나눔)."""

from __future__ import annotations

import asyncio

import pytest

from planning_agent.domain.trace import trace_of, trace_text


def run(coro):
    return asyncio.run(coro)


def acts(service, ctx: str):
    return [a for a in service.state.activity if a.context_id == ctx]


# ---- 역추적·프로젝트·문서


@pytest.mark.role("dev")
def test_trace_of_pr28_chain(service) -> None:
    steps = trace_of(service.state, "PR #28")
    assert [s.kind for s in steps] == [
        "코드",
        "PR",
        "작업",
        "문단",
        "장애",
        "결정",
        "기획서",
        "요청",
        "근거",
    ]
    assert steps[0].title == "shop-api/payment/config.py:12"
    assert steps[0].detail == "PAYMENT_TIMEOUT_MS = 8000"
    assert steps[1].detail == "gen/t-9-payment-timeout · 사람 머지 · 검수 통과"
    assert steps[3].title == "SPEC-5 R3"
    assert steps[4].title == "ctx_inc_0001"
    assert steps[7].title == "REQ-9 느린 카드사에서 결제가 실패한다"
    text = trace_text(steps)
    assert text.startswith("shop-api/payment/config.py:12의 `PAYMENT_TIMEOUT_MS = 8000`는 PR #28")
    assert "SPEC-5 R3" in text and "결제 실패 코드 집계 (데이터분석)" in text


@pytest.mark.role("dev")
def test_queries_and_safety_verify_simulator(service) -> None:
    run(service.inbound("infra_query"))
    reply = service.state.activity[-1]
    assert (reply.from_, reply.to, reply.context_id) == ("planning", "infra", "ctx_inc_0001")
    assert "PR #28" in reply.text and "원인 판정은 하지 않음" in reply.text
    result = run(service.inbound("safety_verify"))
    assert result.output == {"approved": True}
    assert "deploy_before_incident" in service.state.activity[-1].text


@pytest.mark.role("dev")
def test_seed_requests_are_not_advanced(service) -> None:
    before = {x.id: (x.state, x.spec) for x in service.state.requests}
    run(service.inbound("data_query"))
    assert {x.id: (x.state, x.spec) for x in service.state.requests} == before


# ---- 총괄 혼합 모드 시연: 실제 시각·PR 없는 연결


@pytest.mark.role("dev")
def test_link_without_prs_marks_nothing(service) -> None:
    result = run(
        service.link_incident(
            context_id="ctx_inc_empty",
            requester="orchestrator",
            incident_context_id="ctx_inc_empty",
            pr_numbers=[],
            follow_up_title=None,
            idempotency_key="ctx_inc_empty:link_incident:none",
        )
    )
    assert result.output["linked_paras"] == []
    replies = [a.text for a in acts(service, "ctx_inc_empty") if a.to == "orchestrator"]
    assert replies and replies[-1] == "연결할 PR 없음 · 장애 표시 안 함"
    assert not any(p.incident == "ctx_inc_empty" for sp in service.state.specs for p in sp.paras)
