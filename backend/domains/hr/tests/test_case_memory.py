"""이벤트 기억: 해결 내용 기록(`record_resolution`)과 비슷한 과거 이벤트 찾기(`find_similar_cases`)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas import (
    AgentRequest,
    FindSimilarCasesOutput,
    HoldReason,
    RecordCase,
    RecordResolutionOutput,
)
from hr_agent.cards.service import record_case
from hr_agent.case_memory.service import BLOCK, find_similar_cases, record_resolution
from hr_agent.clients.safety import MockGate
from hr_agent.retrieval.cards import DenseCardSearch
from hr_agent.retrieval.dense import DenseIndex

PAYMENT = "결제가 서버 오류로 자꾸 실패해요"
CONTEXT = "ctx_inc_0001"


class MockEmbedder:
    def __init__(self) -> None:
        self._mock = MockLLMClient()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await self._mock.embed(texts)


def _meta(skill: str, ctx: str, key: str, token: str = "mock:orchestrator") -> dict:
    return {
        "context_id": ctx,
        "token": token,
        "deadline": datetime.now(UTC) + timedelta(seconds=30),
        "idempotency_key": f"{ctx}:{skill}:{key}",
    }


def _req(skill: str, inp: dict, ctx: str = CONTEXT, key: str = "k") -> AgentRequest:
    return AgentRequest.model_validate(
        {"meta": _meta(skill, ctx, key), "skill": skill, "input": inp}
    )


async def _incident(svc, ctx: str = CONTEXT, summary: str = PAYMENT, area: str = "infra") -> str:
    record = RecordCase.model_validate(
        {
            "context_id": ctx,
            "kind": "incident",
            "summary": summary,
            "decision_reason": "원인을 확정하지 못해 사람에게 넘김",
            "alternatives_considered": ["즉시 롤백"],
            "source_refs": [f"inc:{ctx}", f"area:{area}"],
            "author": "orchestrator",
        }
    )
    request = AgentRequest.model_validate(
        {
            "meta": _meta("record_case", ctx, "rc", "mock:orchestrator"),
            "skill": "record_case",
            "input": record.model_dump(mode="json"),
        }
    )
    result = await record_case(svc, request)
    assert result.output["outcome"] == "card_created", result.output
    return result.output["card_id"]


def _resolution(**kw) -> dict:
    base = {
        "context_id": CONTEXT,
        "outcome": "resolved",
        "root_cause": "배포 후 결제 설정 누락",
        "action": "직전 버전으로 롤백",
        "minutes_to_resolve": 14,
    }
    return {**base, **kw}


# ---- record_resolution --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolution_is_written_into_the_card_extra_and_body(svc) -> None:
    card_id = await _incident(svc)
    result = await record_resolution(svc, _req("record_resolution", _resolution()))
    out = RecordResolutionOutput.model_validate(result.output)
    assert (out.outcome, out.card_id) == ("recorded", card_id)
    card = await svc.memory.get_card(card_id)
    assert card.extra["resolution"]["root_cause"] == "배포 후 결제 설정 누락"
    assert card.extra["resolution"]["minutes_to_resolve"] == 14
    assert BLOCK.strip() in card.body and "직전 버전으로 롤백" in card.body and "14분" in card.body
    assert "resolved" in card.tags and card.status == "active"
    assert "결정:" in card.body  # 기존 본문은 그대로다


@pytest.mark.asyncio
async def test_missing_values_are_not_invented(svc) -> None:
    card_id = await _incident(svc)
    await record_resolution(
        svc,
        _req("record_resolution", {"context_id": CONTEXT, "outcome": "unresolved"}),
    )
    card = await svc.memory.get_card(card_id)
    assert card.body.count("확인할 수 없음") >= 3  # 원인, 조치, 소요 시간
    assert "resolved" not in card.tags
    assert card.extra["resolution"]["root_cause"] is None


@pytest.mark.asyncio
async def test_same_content_twice_is_written_once(svc) -> None:
    await _incident(svc)
    first = await record_resolution(svc, _req("record_resolution", _resolution(), key="a"))
    second = await record_resolution(svc, _req("record_resolution", _resolution(), key="b"))
    assert first.output["outcome"] == "recorded" and second.output["outcome"] == "duplicate"


@pytest.mark.asyncio
async def test_new_content_replaces_the_block_instead_of_stacking(svc) -> None:
    card_id = await _incident(svc)
    await record_resolution(svc, _req("record_resolution", _resolution(), key="a"))
    result = await record_resolution(
        svc,
        _req(
            "record_resolution", _resolution(action="PG 설정 복구", minutes_to_resolve=9), key="b"
        ),
    )
    assert result.output["outcome"] == "recorded"
    card = await svc.memory.get_card(card_id)
    assert card.body.count(BLOCK.strip()) == 1
    assert "PG 설정 복구" in card.body and "직전 버전으로 롤백" not in card.body


@pytest.mark.asyncio
async def test_no_card_for_the_incident_means_nothing_is_written(svc) -> None:
    result = await record_resolution(
        svc, _req("record_resolution", _resolution(context_id="ctx_inc_none"), ctx="ctx_inc_none")
    )
    assert result.output["outcome"] == "no_card" and result.output["card_id"] is None


@pytest.mark.asyncio
async def test_gate_block_keeps_the_card_unchanged(svc) -> None:
    card_id = await _incident(svc)

    class Blocking(MockGate):
        async def _decide(self, action, params, context, key):
            return self._held("exe_blocked", key, HoldReason.OVER_THRESHOLD)

    svc.gate = Blocking(svc.store, svc.clock)
    result = await record_resolution(svc, _req("record_resolution", _resolution()))
    assert result.outcome == "gate_held"
    card = await svc.memory.get_card(card_id)
    assert "resolution" not in card.extra and BLOCK.strip() not in card.body


@pytest.mark.asyncio
async def test_resolution_needs_an_idempotency_key(svc) -> None:
    await _incident(svc)
    request = _req("record_resolution", _resolution())
    request.meta.idempotency_key = None
    result = await record_resolution(svc, request)
    assert result.status.value in ("failed", "rejected")


@pytest.mark.asyncio
async def test_minutes_are_measured_from_opened_at_not_typed(svc) -> None:
    card_id = await _incident(svc)
    opened = svc.clock() - timedelta(minutes=37, seconds=20)
    result = await record_resolution(
        svc,
        _req(
            "record_resolution",
            {
                "context_id": CONTEXT,
                "outcome": "resolved",
                "action": "재시작",
                "opened_at": opened.isoformat(),
                "prior_case_id": "card_old",
            },
        ),
    )
    assert result.output["outcome"] == "recorded"
    saved = (await svc.memory.get_card(card_id)).extra["resolution"]
    assert saved["minutes_to_resolve"] in (37, 38) and saved["prior_case_id"] == "card_old"
    assert saved["opened_at"] is not None


@pytest.mark.asyncio
async def test_opened_at_in_the_future_is_rejected(svc) -> None:
    await _incident(svc)
    future = svc.clock() + timedelta(hours=1)
    result = await record_resolution(
        svc,
        _req(
            "record_resolution",
            {"context_id": CONTEXT, "outcome": "resolved", "opened_at": future.isoformat()},
        ),
    )
    assert result.status.value in ("failed", "rejected")


@pytest.mark.asyncio
async def test_typed_minutes_win_over_the_measured_ones(svc) -> None:
    card_id = await _incident(svc)
    opened = svc.clock() - timedelta(minutes=10)
    await record_resolution(
        svc,
        _req(
            "record_resolution",
            _resolution(minutes_to_resolve=5, opened_at=opened.isoformat()),
        ),
    )
    assert (await svc.memory.get_card(card_id)).extra["resolution"]["minutes_to_resolve"] == 5


# ---- find_similar_cases ---------------------------------------------------------------------


def _dense(svc, tmp_path) -> None:
    svc.dense = DenseCardSearch(DenseIndex(str(tmp_path / "v.db"), MockEmbedder(), "mock"))
    svc.settings = svc.settings.__class__(**{**svc.settings.__dict__, "dense_min_cosine": 0.3})


async def _search(svc, query: str, **kw) -> FindSimilarCasesOutput:
    result = await find_similar_cases(svc, _req("find_similar_cases", {"query": query, **kw}))
    return FindSimilarCasesOutput.model_validate(result.output)


@pytest.mark.asyncio
async def test_finds_the_past_incident_with_its_resolution(svc, tmp_path) -> None:
    _dense(svc, tmp_path)
    card_id = await _incident(svc)
    await record_resolution(svc, _req("record_resolution", _resolution()))
    out = await _search(svc, "결제가 서버 오류로 실패해요")
    assert out.method == "dense" and [c.card_id for c in out.cases] == [card_id]
    case = out.cases[0]
    assert case.resolved and case.resolution.action == "직전 버전으로 롤백"
    assert case.resolution.minutes_to_resolve == 14
    assert 0 < out.max_similarity <= 1


@pytest.mark.asyncio
async def test_unresolved_cases_are_listed_but_flagged(svc, tmp_path) -> None:
    _dense(svc, tmp_path)
    await _incident(svc)
    case = (await _search(svc, "결제가 서버 오류로 실패해요")).cases[0]
    assert case.resolved is False and case.resolution is None


@pytest.mark.asyncio
async def test_only_resolved_hides_cards_without_a_resolution(svc, tmp_path) -> None:
    _dense(svc, tmp_path)
    await _incident(svc)
    await _incident(svc, ctx="ctx_inc_0002", summary="결제 승인 서버 오류가 반복돼요")
    await record_resolution(svc, _req("record_resolution", _resolution(), ctx=CONTEXT))
    out = await _search(svc, "결제 서버 오류", only_resolved=True)
    assert [c.resolved for c in out.cases] == [True]


@pytest.mark.asyncio
async def test_unrelated_query_returns_nothing(svc, tmp_path) -> None:
    _dense(svc, tmp_path)
    await _incident(svc)
    out = await _search(svc, "회의실 예약 화면 색상")
    assert out.cases == [] and out.max_similarity == 0.0


@pytest.mark.asyncio
async def test_area_filter_limits_the_search(svc, tmp_path) -> None:
    _dense(svc, tmp_path)
    await _incident(svc, area="infra")
    assert (await _search(svc, "결제가 서버 오류로 실패해요", area="search")).cases == []
    assert len((await _search(svc, "결제가 서버 오류로 실패해요", area="infra")).cases) == 1


@pytest.mark.asyncio
async def test_limit_caps_the_result(svc, tmp_path) -> None:
    _dense(svc, tmp_path)
    for n in range(4):
        await _incident(svc, ctx=f"ctx_inc_{n:04d}", summary=f"{PAYMENT} {n}번째")
    out = await _search(svc, "결제가 서버 오류로 실패해요", limit=2)
    assert len(out.cases) == 2


@pytest.mark.asyncio
async def test_falls_back_to_dice_without_embeddings(svc) -> None:
    card_id = await _incident(svc)
    out = await _search(svc, PAYMENT)
    assert out.method == "dice" and [c.card_id for c in out.cases] == [card_id]


@pytest.mark.asyncio
async def test_embedding_failure_falls_back_to_dice(svc, tmp_path) -> None:
    class Broken:
        async def embed(self, texts):
            raise RuntimeError("429")

    svc.dense = DenseCardSearch(DenseIndex(str(tmp_path / "v.db"), Broken(), "mock"))
    card_id = await _incident(svc)
    out = await _search(svc, PAYMENT)
    assert out.method == "dice" and [c.card_id for c in out.cases] == [card_id]


@pytest.mark.asyncio
async def test_verifier_drops_cases_it_judges_irrelevant(svc, tmp_path) -> None:
    _dense(svc, tmp_path)
    card_id = await _incident(svc)

    class Verifier:
        async def verify(self, query, candidates):
            return {cid: False for cid, _ in candidates}

    svc.verifier = Verifier()
    assert (await _search(svc, "결제가 서버 오류로 실패해요")).cases == []
    svc.verifier = None
    assert [c.card_id for c in (await _search(svc, "결제가 서버 오류로 실패해요")).cases] == [
        card_id
    ]


@pytest.mark.asyncio
async def test_policy_and_notice_cards_are_not_returned(svc, tmp_path) -> None:
    _dense(svc, tmp_path)
    record = RecordCase.model_validate(
        {
            "context_id": "ctx_n",
            "kind": "notice",
            "summary": "결제가 서버 오류로 자꾸 실패해요 공지",
            "decision_reason": "고객 안내",
            "alternatives_considered": ["공지 안 함"],
            "source_refs": ["n:1", "area:infra"],
            "author": "marketing",
        }
    )
    request = AgentRequest.model_validate(
        {
            "meta": _meta("record_case", "ctx_n", "rc", "mock:marketing"),
            "skill": "record_case",
            "input": record.model_dump(mode="json"),
        }
    )
    await record_case(svc, request)
    assert (await _search(svc, "결제가 서버 오류로 실패해요")).cases == []
