"""기획 제안: 데이터분석 인사이트(근거 수치) → 기획 판단(할 일·이유·목표) → 사람 확정·폐기
→ 마케팅 또는 개발 → 성과 기록 (11/4 데이터분석·기획 합의 원칙)."""

from __future__ import annotations

import asyncio

import pytest

from ax_contracts.schemas import AgentResult, ExecuteDecision, TaskStatus
from ax_contracts.schemas.data import PromoConceptsOutput
from ax_contracts.schemas.planning import (
    CampaignBriefOutput,
    InsightFact,
    ProposalTarget,
    ReceiveCampaignUpdateInput,
    ReceiveInsightInput,
)
from planning_agent import proposals
from planning_agent.domain.service import ActionError, PlanningService


async def _gate(action, params, meta, evidence) -> ExecuteDecision:
    return ExecuteDecision(execution_id="exe_ok", grade="A", status="executed")


FACT = {"label": "독서 검색 작년 11월", "value": "평균의 1.6배", "source": "trend_history"}


def _insight(ref: str = "ins-1", body: str | None = None) -> ReceiveInsightInput:
    return ReceiveInsightInput.model_validate(
        {
            "insight_id": ref,
            "title": "가을 밤 10분 독서 숏츠",
            "summary": "독서 검색이 11월에 오르고 루틴 브이로그 숏츠 조회가 높다",
            "skill": "trend_history",
            "facts": [FACT],
            "products": ["밤의 도서관"],
            "body_md": body,
        }
    )


class Brief:
    def __init__(self, out=None) -> None:
        self.sent: list = []
        self.out = out or CampaignBriefOutput(outcome="accepted", campaign_id="CMP-1")

    async def send(self, ctx, brief, key):
        self.sent.append((ctx, brief, key))
        return self.out


class Writer:
    """LLM 대신: 할 일·이유·목표를 정해 돌려준다 (이유에 근거에 없는 숫자 3을 섞는다)."""

    def __init__(self) -> None:
        self.calls = 0

    async def draft(self, inp, ctx):
        self.calls += 1
        target = ProposalTarget(
            label="캠페인 기간 주문 증분", value="+10%", measure="campaign_effect"
        )
        why = "독서 검색이 평균의 1.6배이고 3주 연속 오른다"
        return proposals.ProposalDraft("루틴 브이로그 숏츠", why, target, "숏폼 30초", "llm")


def _svc(marketing=None, insights=None, writer=None) -> PlanningService:
    return PlanningService(
        gate=_gate, relater=None, insights=insights, marketing=marketing, proposal_writer=writer
    )


def _p(svc: PlanningService, pid: str):
    return next(x for x in svc.state.proposals if x.id == pid)


async def _flush(svc: PlanningService) -> None:
    while svc._sending:
        await asyncio.gather(*list(svc._sending))


def _new(svc: PlanningService, **kw) -> str:
    res = asyncio.run(svc.receive_insight(requester="data", inp=_insight(**kw)))
    return res.output["proposal_id"]


@pytest.mark.role("planning")
def test_pull_skips_similar_proposals_even_with_new_ids() -> None:
    class Pull:
        def __init__(self, items):
            self.items = items

        async def pull(self, ctx):
            return self.items

    same_goods = _insight("promo_b:pc_2").model_copy(update={"title": "밤의 도서관 루틴 숏츠"})
    same_title = _insight("promo_b:pc_3").model_copy(
        update={"title": "가을 밤 10분 독서 숏츠!", "products": ["다른 책"], "facts": []}
    )
    other = _insight("promo_b:pc_4").model_copy(
        update={"title": "겨울 다이어리 꾸미기", "products": ["무지 노트 3권 세트"]}
    )
    svc = _svc(insights=Pull([same_goods, same_title, other]))
    first = _new(svc, ref="promo_a:pc_1")
    res = asyncio.run(svc.dispatch({"type": "pullInsights"}))
    out = res.output
    assert [x["proposal_id"] for x in out["similar"]] == [first, first]
    assert len(out["created"]) == 1 and len(svc.state.proposals) == 2
    assert "비슷한 제안이 있어 건너뜀 2개" in svc.state.activity[-1].text


@pytest.mark.role("planning")
def test_rule_draft_quotes_facts_and_dedups_without_rewriting() -> None:
    writer = Writer()
    svc = _svc()
    pid = _new(svc)
    p = _p(svc, pid)
    assert p.status == "확인 대기" and p.written_by == "rule" and p.target is None
    assert "평균의 1.6배" in p.why and p.insight_id == "ins-1" and not p.warnings
    svc._proposal_writer = writer
    again = asyncio.run(svc.receive_insight(requester="data", inp=_insight()))
    assert again.outcome == "duplicate" and again.output["proposal_id"] == pid
    assert writer.calls == 0 and len(svc.state.proposals) == 1  # 재전송은 LLM을 다시 부르지 않는다


@pytest.mark.role("planning")
def test_llm_draft_sets_target_and_warns_unknown_numbers() -> None:
    svc = _svc(writer=Writer())
    p = _p(svc, _new(svc, body="작년보다 2배 늘 것"))
    assert p.written_by == "llm" and p.target.value == "+10%" and p.format == "숏폼 30초"
    joined = " / ".join(p.warnings)
    assert "이유의 숫자 3" in joined  # 3주는 근거에 없다
    assert "설명의 숫자 2" in joined  # 본문 숫자도 대조한다
    assert "1.6" not in joined and "10" not in joined  # 근거·목표 숫자는 통과


@pytest.mark.role("planning")
def test_contract_needs_facts_and_carries_schema_version() -> None:
    with pytest.raises(ValueError):
        ReceiveInsightInput.model_validate(
            {"insight_id": "x", "title": "t", "summary": "s", "skill": "k", "facts": []}
        )
    assert _insight().schema_version == "1"


@pytest.mark.role("planning")
def test_confirm_to_marketing_sends_brief_with_id_chain() -> None:
    brief = Brief()
    svc = _svc(marketing=brief, writer=Writer())

    async def go():
        pid = (await svc.receive_insight(requester="data", inp=_insight())).output["proposal_id"]
        await svc.dispatch({"type": "approveProposal", "proposal": pid, "note": "11월 둘째 주"})
        await _flush(svc)
        return pid

    pid = asyncio.run(go())
    p = _p(svc, pid)
    assert p.status == "마케팅 전달" and p.handoff.ok and p.handoff.campaign == "CMP-1"
    _, sent, key = brief.sent[0]
    assert sent.proposal_id == pid and sent.insight_id == "ins-1" and sent.target.value == "+10%"
    assert sent.schema_version == "1" and key == f"proposal:{pid}:brief"


@pytest.mark.role("planning")
def test_marketing_failure_is_kept_not_invented() -> None:
    class Down(Brief):
        async def send(self, ctx, brief, key):
            return "호출 실패 (ConnectError)"

    svc = _svc(marketing=Down())

    async def go():
        pid = (await svc.receive_insight(requester="data", inp=_insight())).output["proposal_id"]
        await svc.dispatch({"type": "approveProposal", "proposal": pid})
        await _flush(svc)
        return pid

    p = _p(svc, asyncio.run(go()))
    assert p.handoff is not None and not p.handoff.ok and "ConnectError" in p.handoff.reason


@pytest.mark.role("planning")
def test_human_edit_rechecks_numbers_then_discard_locks() -> None:
    svc = _svc()
    pid = _new(svc)
    edit = {"type": "editProposal", "proposal": pid, "why": "검색이 5배 올랐다"}
    asyncio.run(svc.dispatch(edit))
    p = _p(svc, pid)
    assert p.written_by == "human" and any("5" in w for w in p.warnings)
    target = {"targetLabel": "주문 증분", "targetValue": "+5%"}
    asyncio.run(svc.dispatch({"type": "editProposal", "proposal": pid, **target}))
    assert _p(svc, pid).warnings == []  # 목표치에 5가 들어가 경고가 풀린다
    asyncio.run(svc.dispatch({"type": "rejectProposal", "proposal": pid, "reason": "시즌 지남"}))
    p = _p(svc, pid)
    assert p.status == "폐기" and p.note == "시즌 지남"
    asyncio.run(svc.dispatch({"type": "editProposal", "proposal": pid, "title": "또"}))
    assert _p(svc, pid).title == "가을 밤 10분 독서 숏츠"


@pytest.mark.role("planning")
def test_confirm_to_dev_registers_request() -> None:
    svc = _svc(writer=Writer())
    pid = _new(svc)
    out = asyncio.run(svc.dispatch({"type": "proposalToRequest", "proposal": pid}))
    req_id = out.output["request_id"]
    p = _p(svc, pid)
    assert p.status == "기획서로" and p.request == req_id
    req = next(r for r in svc.state.requests if r.id == req_id)
    assert req.title == p.title and "평균의 1.6배" in req.evidence and "+10%" in req.evidence
    with pytest.raises(ActionError):
        asyncio.run(svc.dispatch({"type": "proposalToRequest", "proposal": pid}))


@pytest.mark.role("planning")
def test_published_by_marketing_then_result_by_data() -> None:
    svc = _svc()
    pid = _new(svc)
    asyncio.run(svc.dispatch({"type": "approveProposal", "proposal": pid}))

    def up(**k):
        return ReceiveCampaignUpdateInput.model_validate(
            {"proposal_id": pid, "campaign_id": "CMP-7", **k}
        )

    pub = up(stage="published", channel="sns", post_ref="post-9", insight_id="ins-1")
    res = asyncio.run(svc.receive_campaign_update(inp=pub, requester="marketing"))
    assert res.outcome == "applied" and _p(svc, pid).status == "진행 중"
    assert _p(svc, pid).handoff.channel == "sns" and _p(svc, pid).handoff.post_ref == "post-9"
    fact = InsightFact(label="캠페인 기간 주문", value="+18%", source="campaign_effect")
    result = up(stage="result", direction="늘었음", summary="주문 +18%", facts=[fact])
    res = asyncio.run(svc.receive_campaign_update(inp=result, requester="data"))
    p = _p(svc, pid)
    assert res.outcome == "applied" and p.status == "성과 확인" and p.result.direction == "늘었음"
    assert p.result.facts[0].value == "+18%" and p.handoff.campaign == "CMP-7"
    assert asyncio.run(svc.receive_campaign_update(inp=result, requester="data")).outcome == (
        "duplicate"
    )
    unknown = up(stage="published").model_copy(update={"proposal_id": "PROP-99"})
    assert asyncio.run(svc.receive_campaign_update(inp=unknown)).outcome == "unknown_proposal"


@pytest.mark.role("planning")
def test_pull_insights_off_in_mock_and_converts_promo() -> None:
    with pytest.raises(ActionError):
        asyncio.run(_svc().dispatch({"type": "pullInsights"}))

    promo = PromoConceptsOutput.model_validate(
        {
            "concepts": [
                {
                    "concept_id": "c1", "title": "밤 독서 루틴", "concept": "브이로그",
                    "borrowed_trend": "독서",
                    "products": [{"product_id": "p1", "name": "밤의 도서관", "price": 16800}],
                    "duration_seconds": 30, "shorts_format": "루틴 브이로그",
                    "shorts_format_avg_views": 42000, "storyboard": ["불 끄기", "책 펴기"],
                },
                {"concept_id": "c2", "title": "근거 없음", "borrowed_trend": "없는 트렌드"},
            ],
            "trends": [
                {"name": "독서", "kind": "season", "detail": "작년 11월 검색이 평균의 1.6배"}
            ],
            "run_id": "run-1",
        }
    )  # fmt: skip

    async def caller(target, req):
        if req.skill == "trend_insight_brief":  # 아직 없는 skill → promo_concepts로 대신
            raise RuntimeError("unknown skill")
        assert target == "data" and req.skill == "promo_concepts"
        out = promo.model_dump(mode="json")
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output=out)

    svc = _svc(insights=proposals.DataInsights(caller=caller))
    out = asyncio.run(svc.dispatch({"type": "pullInsights"}))
    assert out.output["received"] == 1 and len(out.output["created"]) == 1  # 근거 없는 초안은 뺀다
    p = _p(svc, out.output["created"][0])
    values = {f.value for f in p.facts}
    assert {"작년 11월 검색이 평균의 1.6배", "4.2만", "16,800원"} <= values
    assert p.insight_id == "run-1:c1" and "콘티: 불 끄기" in (p.body_md or "")
    again = asyncio.run(svc.dispatch({"type": "pullInsights"}))
    assert again.output["created"] == []  # 같은 초안은 다시 만들지 않는다


@pytest.mark.role("planning")
def test_confirm_makes_plan_spec_that_waits_for_human_route() -> None:
    svc = _svc(writer=Writer())
    pid = _new(svc, body="## 설명" + chr(10) + "데이터분석 메모")
    out = asyncio.run(svc.dispatch({"type": "makePlan", "proposal": pid}))
    spec_id, req_id = out.output["spec_id"], out.output["request_id"]
    p = _p(svc, pid)
    assert p.status == "기획안" and p.spec == spec_id and p.request == req_id
    sp = next(x for x in svc.state.specs if x.id == spec_id)
    assert sp.origin == "proposal" and sp.status == "초안" and sp.title == p.title
    assert p.insight in sp.problem and sp.paras[0].text == p.idea
    assert any("+10%" in x.text for x in sp.paras if x.kind == "done")
    assert "데이터분석 메모" in (sp.body_md or "") and any("평균의 1.6배" in b for b in sp.basis)
    req = next(r for r in svc.state.requests if r.id == req_id)
    assert req.state == "기획 중" and req.spec == spec_id and req.source_ref == pid
    asyncio.run(svc.dispatch({"type": "checkSpec", "spec": spec_id}))
    assert next(x for x in svc.state.specs if x.id == spec_id).status == "초안"  # 자동 확정 안 함
    again = asyncio.run(svc.dispatch({"type": "makePlan", "proposal": pid}))
    assert not again.changed


@pytest.mark.role("planning")
def test_plan_to_dev_confirms_spec_and_splits_tasks() -> None:
    svc = _svc(writer=Writer())
    pid = _new(svc)
    spec_id = asyncio.run(svc.dispatch({"type": "makePlan", "proposal": pid})).output["spec_id"]
    asyncio.run(svc.dispatch({"type": "proposalToRequest", "proposal": pid}))
    p = _p(svc, pid)
    sp = next(x for x in svc.state.specs if x.id == spec_id)
    assert p.status == "기획서로" and sp.status == "확정"
    assert any(t.spec == spec_id for t in svc.state.tasks)


@pytest.mark.role("planning")
def test_pull_prefers_trend_insight_brief() -> None:
    brief = {
        "status": "known",
        "generated_at": "2026-11-04T01:00:00Z",
        "limits": ["상대값"],
        "insights": [
            {
                "insight_id": "2026-11-04:audiobook", "rank": 1, "topic": "오디오북",
                "line": "오디오북 검색이 평소의 2.3배",
                "evidence": {"naver_ratio": 2.3, "youtube_videos": 7,
                             "links": ["https://youtu.be/x"]},
                "target_age": "20대", "target_age_ratio": 1.8,
                "products": [
                    {"product_id": "p1", "name": "오디오북 이용권", "reason": "이름 일치"}
                ],
                "shorts_format": "책 속 한 줄", "shorts_format_median_views": 15000,
                "example_ideas": ["출근길 한 줄"],
            },
            {"insight_id": "x2", "rank": 2, "topic": "근거 없음", "line": "x"},
            {"insight_id": "x3", "rank": 3, "topic": "참고", "line": "y", "reference_only": True,
             "evidence": {"naver_ratio": 1.5}},
        ],
    }  # fmt: skip

    async def caller(target, req):
        assert req.skill == "trend_insight_brief"
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output=brief)

    svc = _svc(insights=proposals.DataInsights(caller=caller))
    out = asyncio.run(svc.dispatch({"type": "pullInsights"}))
    assert out.output["received"] == 1  # 근거 없음·참고용은 뺀다
    p = _p(svc, out.output["created"][0])
    assert p.insight_id == "2026-11-04:audiobook" and p.title == "오디오북"
    assert p.audience == "20대" and p.shorts_format == "책 속 한 줄"
    values = {f.value for f in p.facts}
    assert {"2.3배", "7개", "1.8배", "1.5만"} <= values
    assert next(f for f in p.facts if f.value == "7개").link == "https://youtu.be/x"
    assert p.products == ["오디오북 이용권"] and "출근길 한 줄" in (p.body_md or "")


@pytest.mark.role("planning")
def test_measure_sends_ids_then_retries_without_and_records_result() -> None:
    effect = {
        "campaign_id": "CMP-1",
        "window_from": "2026-11-04T00:00:00Z",
        "window_to": "2026-11-05T00:00:00Z",
        "orders": {"status": "known", "direction": "up", "actual": 130, "expected": 100,
                   "excess": 30, "ratio": 1.3, "baseline_days": 7},
        "revenue": {"status": "unknown", "unknown_reason": "매출 지표 없음"},
        "coupons": {"status": "known", "coupon_count": 10, "issued_units": 10, "used_units": 3,
                    "use_rate": 0.3},
        "summary": "주문이 기대보다 30건 많았다",
    }  # fmt: skip
    seen: list[dict] = []

    async def caller(target, req):
        assert req.skill == "campaign_effect"
        seen.append(dict(req.input))
        if "insight_id" in req.input:  # 지금 데이터분석 계약은 모르는 칸을 거부한다
            raise RuntimeError("extra fields not permitted")
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output=effect)

    svc = _svc(marketing=Brief(), insights=proposals.DataInsights(caller=caller))

    async def go():
        pid = (await svc.receive_insight(requester="data", inp=_insight())).output["proposal_id"]
        await svc.dispatch({"type": "makePlan", "proposal": pid})
        await svc.dispatch({"type": "approveProposal", "proposal": pid})
        await _flush(svc)
        await svc.dispatch({"type": "measureProposal", "proposal": pid})
        return pid

    p = _p(svc, asyncio.run(go()))
    assert seen[0]["insight_id"] == "ins-1" and seen[0]["proposal_id"] == p.id
    assert seen[0]["campaign_id"] == "CMP-1" and "insight_id" not in seen[1]
    assert p.status == "성과 확인" and p.result.direction == "늘었음"
    values = {f.value for f in p.result.facts}
    assert {"+30건 (기대 100건)", "30%"} <= values


def test_effect_result_uses_products_basis_and_keeps_groups_apart() -> None:
    from ax_contracts.schemas.data import CampaignEffectOutput

    def measure(inc, low, high, base, d):
        return {"period": base + inc, "baseline": base, "incremental": inc,
                "incremental_low": low, "incremental_high": high, "direction": d}  # fmt: skip

    note = "무지 노트 3권 세트"
    pe = {
        "status": "known",
        "matched": [{"product_id": "p1", "name": note, "asked": note}],
        "unmatched_products": ["없는 상품"],
        "orders": measure(12, 5, 20, 30, "up"),
        "units": measure(15, 6, 24, 40, "up"),
        "gross_krw": measure(112500, 45000, 180000, 300000, "up"),
    }

    def out(products_effect: dict) -> CampaignEffectOutput:
        return CampaignEffectOutput.model_validate({
            "campaign_id": "cmp_PROP-1",
            "window_from": "2026-11-04T00:00:00Z", "window_to": "2026-11-05T00:00:00Z",
            "orders": {"status": "known", "direction": "down", "actual": 80, "expected": 100,
                       "excess": -20, "ratio": 0.8, "baseline_days": 7},
            "revenue": {"status": "unknown", "unknown_reason": "매출 지표 없음"},
            "coupons": {"status": "unknown", "unknown_reason": "캠페인 쿠폰이 없음"},
            "summary": "관련 상품 주문이 늘었다",
            "products_effect": products_effect,
        })  # fmt: skip

    direction, _, facts = proposals.effect_result(out(pe))
    assert direction == "늘었음"  # 가게 전체는 줄었어도 방향은 상품 기준
    by = {f.label: (f.value, f.source) for f in facts}
    got = by["관련 상품 주문 증분"]
    assert got == ("+12건 (기준 30건, 범위 +5~+20)", proposals.EFFECT_PRODUCTS)
    assert by["관련 상품 판매액 증분"][0].startswith("+112,500원")
    assert by["못 찾은 상품"] == ("없는 상품", proposals.EFFECT_PRODUCTS)
    assert by["가게 전체 주문 증분"] == ("-20건 (기대 100건)", proposals.EFFECT_STORE)
    flat = {**pe, "orders": measure(2, -4, 8, 30, "uncertain")}
    assert proposals.effect_result(out(flat))[0] == "같음"
    gone = {**pe, "status": "unknown", "unknown_reason": "기록 공백"}
    d, _, f = proposals.effect_result(out(gone))
    assert d == "확인 불가" and any(x.value == "기록 공백" for x in f)


@pytest.mark.role("planning")
def test_effect_result_shows_attribution_apart_and_keeps_direction() -> None:
    """유입 기준(#648)은 따로 묶고, 기준선 없는 직접 귀속이라 방향은 바꾸지 않는다."""
    from ax_contracts.schemas.data import CampaignEffectOutput

    base = {
        "campaign_id": "cmp_PROP-1",
        "window_from": "2026-11-04T00:00:00Z", "window_to": "2026-11-05T00:00:00Z",
        "orders": {"status": "known", "direction": "none", "actual": 100, "expected": 100,
                   "excess": 0, "ratio": 1.0, "baseline_days": 7},
        "revenue": {"status": "unknown", "unknown_reason": "매출 지표 없음"},
        "coupons": {"status": "unknown", "unknown_reason": "캠페인 쿠폰이 없음"},
        "summary": "링크로 온 주문이 있다",
    }  # fmt: skip
    known = {
        **base,
        "attribution_effect": {
            "status": "known", "campaign_id": "cmp_PROP-1", "orders": 7, "gross_krw": 52500,
            "by_source": [{"source": "instagram", "mediums": ["social"], "orders": 5,
                           "gross_krw": 37500}],
            "window_orders": 100, "tagged_orders": 20, "tagged_share": 0.2,
        },
        "attribution_and_products": {"status": "known", "matched": ["p1"], "orders": 4,
                                     "units": 4, "gross_krw": 30000},
    }  # fmt: skip
    direction, _, facts = proposals.effect_result(CampaignEffectOutput.model_validate(known))
    assert direction == "같음"  # 가게 전체 기준 그대로 (유입은 방향에 안 씀)
    by = {f.label: (f.value, f.source) for f in facts}
    assert by["캠페인 링크로 온 주문"] == ("7건 · 52,500원", proposals.EFFECT_ATTRIBUTION)
    assert by["출처 instagram"][0] == "5건 · 37,500원"
    assert by["그중 관련 상품 주문"][0] == "4건 · 30,000원"
    assert by["유입 표시가 있는 주문 비율"][0] == "20%"
    none = {**base, "attribution_effect": {"status": "unknown", "unknown_reason": "유입 기록 없음"}}
    _, _, f = proposals.effect_result(CampaignEffectOutput.model_validate(none))
    assert [(x.label, x.value) for x in f if x.source == proposals.EFFECT_ATTRIBUTION] == [
        ("유입 기준 확인 불가", "유입 기록 없음")
    ]


@pytest.mark.role("planning")
def test_measure_sends_proposal_products() -> None:
    seen: list[dict] = []
    effect = {
        "campaign_id": "CMP-1",
        "window_from": "2026-11-04T00:00:00Z", "window_to": "2026-11-05T00:00:00Z",
        "orders": {"status": "unknown", "unknown_reason": "기준선 부족"},
        "revenue": {"status": "unknown", "unknown_reason": "매출 지표 없음"},
        "coupons": {"status": "unknown", "unknown_reason": "캠페인 쿠폰이 없음"},
        "summary": "확인할 수 없음",
    }  # fmt: skip

    async def caller(target, req):
        seen.append(dict(req.input))
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output=effect)

    svc = _svc(marketing=Brief(), insights=proposals.DataInsights(caller=caller))

    async def go():
        pid = (await svc.receive_insight(requester="data", inp=_insight())).output["proposal_id"]
        await svc.dispatch({"type": "makePlan", "proposal": pid})
        await svc.dispatch({"type": "approveProposal", "proposal": pid})
        await _flush(svc)
        await svc.dispatch({"type": "measureProposal", "proposal": pid})
        return pid

    p = _p(svc, asyncio.run(go()))
    assert seen[0]["products"] == p.products and p.products


def test_effect_window_starts_at_publish_and_caps_at_seven_days() -> None:
    from datetime import UTC, datetime, timedelta

    from ax_workflow.models import ProposalHandoff
    from planning_agent.domain.proposal_actions import effect_window

    made = datetime(2026, 11, 1, tzinfo=UTC)
    sent = made + timedelta(hours=2)
    posted = sent + timedelta(days=1)
    now = posted + timedelta(days=2)
    handed = ProposalHandoff(ok=True, campaign="cmp_PROP-1", at=sent)
    assert effect_window(handed, made, now) == (sent, now)  # 게시 알림 전: 넘긴 시각부터
    shown = handed.model_copy(update={"published_at": posted})
    assert effect_window(shown, made, now) == (posted, now)  # 게시 시각부터
    late = posted + timedelta(days=30)
    assert effect_window(shown, made, late) == (posted, posted + timedelta(days=7))  # 7일에서 자름
    assert effect_window(ProposalHandoff(ok=True), made, now)[0] == made


@pytest.mark.role("planning")
def test_measure_needs_campaign() -> None:
    svc = _svc()
    pid = _new(svc)
    with pytest.raises(ActionError):
        asyncio.run(svc.dispatch({"type": "measureProposal", "proposal": pid}))


@pytest.mark.role("planning")
def test_plan_spec_with_free_md_goes_to_marketing_without_dev() -> None:
    brief = Brief()
    svc = _svc(marketing=brief)

    async def go():
        pid = (await svc.receive_insight(requester="data", inp=_insight())).output["proposal_id"]
        spec = (await svc.dispatch({"type": "makePlan", "proposal": pid})).output["spec_id"]
        edit = {
            "type": "editSpec",
            "spec": spec,
            "body_md": "## 촬영 메모" + chr(10) + "조명은 스탠드 하나",
        }
        await svc.dispatch(edit)
        await svc.dispatch({"type": "approveProposal", "proposal": pid})
        await _flush(svc)
        return spec

    spec = asyncio.run(go())
    _, sent, _ = brief.sent[0]
    assert "조명은 스탠드 하나" in sent.body_md and sent.body_md.startswith("# ")
    sp = next(x for x in svc.state.specs if x.id == spec)
    assert sp.status == "확정" and not sp.handed_to_dev
    assert not any(t.spec == spec for t in svc.state.tasks)  # 마케팅 실행이라 개발 작업 없음


@pytest.mark.role("planning")
def test_failed_handoff_reverts_to_plan_and_can_retry() -> None:
    class Down(Brief):
        async def send(self, ctx, brief, key):
            self.sent.append(key)
            return CampaignBriefOutput(outcome="rejected", reason_code="FORBIDDEN_SKILL")

    down = Down()
    svc = _svc(marketing=down)

    async def go():
        pid = (await svc.receive_insight(requester="data", inp=_insight())).output["proposal_id"]
        await svc.dispatch({"type": "makePlan", "proposal": pid})
        await svc.dispatch({"type": "approveProposal", "proposal": pid})
        await _flush(svc)
        return pid

    pid = asyncio.run(go())
    p = _p(svc, pid)
    sp = next(x for x in svc.state.specs if x.id == p.spec)
    assert p.status == "기획안" and not p.handoff.ok and "FORBIDDEN_SKILL" in p.handoff.reason
    assert sp.status == "초안"  # 못 넘겼으니 기획안 기획서도 초안으로
    svc._marketing = Brief()

    async def retry():
        await svc.dispatch({"type": "approveProposal", "proposal": pid})
        await _flush(svc)

    asyncio.run(retry())
    p = _p(svc, pid)
    assert p.status == "마케팅 전달" and p.handoff.ok and p.handoff.campaign == "CMP-1"


@pytest.mark.role("planning")
def test_promo_reference_only_concept_is_skipped() -> None:
    promo = PromoConceptsOutput.model_validate(
        {
            "concepts": [
                {
                    "concept_id": "c1", "title": "골프 입문", "borrowed_trend": "골프",
                    "products": [{"product_id": "p1", "name": "골프 첫걸음", "price": 15000}],
                    "reference_only": True, "reference_reason": "회사 도메인과 연결 없음",
                },
                {
                    "concept_id": "c2", "title": "밤 독서", "borrowed_trend": "독서",
                    "products": [{"product_id": "p2", "name": "밤의 도서관", "price": 16800}],
                },
            ],
            "trends": [{"name": "독서", "kind": "season", "detail": "평균의 1.6배"}],
            "run_id": "run-2",
        }
    )  # fmt: skip
    got = proposals.from_promo(promo)
    assert [x.insight_id for x in got] == ["run-2:c2"]


@pytest.mark.role("planning")
def test_delete_proposal_removes_plan_and_request_but_not_after_dev() -> None:
    svc = _svc(writer=Writer())
    pid = _new(svc)
    out = asyncio.run(svc.dispatch({"type": "makePlan", "proposal": pid})).output
    asyncio.run(svc.dispatch({"type": "deleteProposal", "proposal": pid}))
    assert all(x.id != pid for x in svc.state.proposals)
    assert all(x.id != out["spec_id"] for x in svc.state.specs)
    assert all(r.id != out["request_id"] for r in svc.state.requests)
    assert svc.state.activity[-1].point == "delete_proposal"

    pid2 = _new(svc, ref="ins-2")
    asyncio.run(svc.dispatch({"type": "makePlan", "proposal": pid2}))
    asyncio.run(svc.dispatch({"type": "proposalToRequest", "proposal": pid2}))
    with pytest.raises(ActionError):
        asyncio.run(svc.dispatch({"type": "deleteProposal", "proposal": pid2}))
    assert any(x.id == pid2 for x in svc.state.proposals)


@pytest.mark.role("planning")
def test_delete_proposal_is_blocked_after_marketing_handoff() -> None:
    """마케팅에 넘긴(전달 성공) 제안은 지우지 않는다. 전달 실패면 지울 수 있다."""
    from ax_workflow.models import ProposalHandoff

    svc = _svc(writer=Writer())
    sent, failed = _new(svc), _new(svc, ref="ins-3")
    for pid in (sent, failed):
        asyncio.run(svc.dispatch({"type": "makePlan", "proposal": pid}))
    by = {p.id: p for p in svc.state.proposals}
    by[sent].handoff = ProposalHandoff(ok=True, campaign="cmp-1")
    by[failed].handoff = ProposalHandoff(ok=False, reason="마케팅 응답 없음")
    with pytest.raises(ActionError) as exc:
        asyncio.run(svc.dispatch({"type": "deleteProposal", "proposal": sent}))
    assert exc.value.code == "PROPOSAL_SENT"
    asyncio.run(svc.dispatch({"type": "deleteProposal", "proposal": failed}))
    assert [p.id for p in svc.state.proposals] == [sent]
