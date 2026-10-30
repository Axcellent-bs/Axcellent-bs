"""답변 근거를 memory 정책 카드에서 받는다 (이슈 #193)."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from support_agent import kb, policy_memory

_REAL_FETCH = policy_memory.fetch_sources  # conftest가 기본으로 바꿔 둔 것을 되돌린다


@pytest.fixture(autouse=True)
def _real_memory_search(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(policy_memory, "fetch_sources", _REAL_FETCH)


def _request() -> AgentRequest:
    return AgentRequest.model_validate(
        {
            "meta": {
                "context_id": "ctx_inq_0001",
                "token": "mock:support",
                "path": [],
                "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
                "idempotency_key": "k-1",
            },
            "skill": "create_inquiry",
            "input": {},
        }
    )


def _hit(card_id: str, *, score: float = 0.9, low: bool = False, status: str = "active") -> dict:
    return {
        "card": {
            "card_id": card_id,
            "kind": "policy",
            "title": "배송 정책: 지연 기준",
            "body": "배송이 3일 이상 늦으면 안내한다.",
            "tags": ["policy", "shipping"],
            "status": status,
        },
        "score": score,
        "low_confidence": low,
    }


def _sender(
    hits: Any, status: TaskStatus = TaskStatus.COMPLETED
) -> tuple[list[tuple[str, AgentRequest]], Any]:
    sent: list[tuple[str, AgentRequest]] = []

    async def send(target: str, req: AgentRequest) -> AgentResult:
        sent.append((target, req))
        return AgentResult(meta=req.meta, status=status, output={"hits": hits})

    return sent, send


def test_policy_cards_become_sources_with_a_read_only_card_search() -> None:
    sent, send = _sender([_hit("crd_1")])
    sources = asyncio.run(policy_memory.search(_request(), "배송 지연", send))
    assert [s.id for s in sources] == ["crd_1"]
    assert (sources[0].doc_id, sources[0].category) == ("shipping", "shipping")
    assert (sources[0].title, sources[0].section) == ("배송 정책", "지연 기준")
    assert sources[0].score == 0.9
    target, req = sent[0]
    assert target == "memory" and req.skill == "card_search"
    assert req.input == {"query": "배송 지연", "kind": ["policy"], "top_k": policy_memory.FETCH_K}
    assert req.meta.idempotency_key is None  # 읽기라 쓰기용 키를 싣지 않는다


def test_low_confidence_and_inactive_cards_are_not_evidence() -> None:
    _, send = _sender([_hit("a", low=True), _hit("b", status="superseded"), _hit("c")])
    found = asyncio.run(policy_memory.fetch_sources(_request(), "배송", send))
    assert [s.id for s in found or []] == ["c"]


@pytest.mark.parametrize(
    "hits,status",
    [
        ([], TaskStatus.COMPLETED),  # 쓸 카드가 없다
        ([_hit("x", low=True)], TaskStatus.COMPLETED),  # 전부 확신도 낮음
        (None, TaskStatus.COMPLETED),  # 출력 모양이 다르다
        ([_hit("x")], TaskStatus.FAILED),  # memory가 거절했다
    ],
)
def test_falls_back_to_the_seed_when_memory_has_nothing_usable(hits: Any, status: Any) -> None:
    _, send = _sender(hits, status)
    sources = asyncio.run(policy_memory.search(_request(), "불량 환불", send))
    assert sources == kb.search("불량 환불", limit=3)
    assert sources and all(s.id.startswith("refund-") for s in sources)


def test_falls_back_to_the_seed_when_memory_is_unreachable() -> None:
    async def broken(target: str, req: AgentRequest) -> AgentResult:
        raise ConnectionError("memory down")

    assert asyncio.run(policy_memory.fetch_sources(_request(), "배송", broken)) is None
    sources = asyncio.run(policy_memory.search(_request(), "불량 환불", broken))
    assert sources == kb.search("불량 환불", limit=3)


def test_an_empty_query_does_not_call_memory() -> None:
    sent, send = _sender([_hit("crd_1")])
    assert asyncio.run(policy_memory.search(_request(), "   ", send)) == []
    assert sent == []


def test_unconfirmed_cards_do_not_use_up_the_evidence_slots() -> None:
    # memory는 넉넉히 보내고, 미확정 카드를 뺀 뒤에 limit장으로 자른다
    hits = [_hit("low1", low=True), _hit("low2", low=True)]
    hits += [_hit(f"ok{i}") for i in range(1, 6)]
    _, send = _sender(hits)
    found = asyncio.run(policy_memory.fetch_sources(_request(), "개인정보 삭제", send))
    assert [s.id for s in found or []] == ["ok1", "ok2", "ok3"]
