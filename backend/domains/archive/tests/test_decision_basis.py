"""아카이브 AI의 `find_similar_decisions`: 사유와 비슷한 과거 업무 결정 카드 찾기(RAG, 조회 전용).

인사 AI의 발령 입력 화면이 근거 후보를 보여 주려고 부른다. 카드를 근거로 저장하는 일
(`record_org_change`)은 인사 AI의 일이라 인사 시험(`domains/hr/tests/test_org_rag_llm.py`)이 본다.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from archive_agent.config import ArchiveSettings, ManualClock
from archive_agent.main import app
from archive_agent.retrieval.cards import DenseCardSearch
from archive_agent.retrieval.dense import DenseIndex
from archive_agent.services import build_services, set_services
from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas import FindSimilarDecisionsOutput

_keys = count(1)

STAFF = (
    "spec_confirmed",
    "개발팀 인력 부족으로 기획팀 1명을 개발팀으로 이동하기로 확정했다",
    "일정 지연을 줄이기 위해",
    ["현행 유지"],
)
REJECTED = (
    "decision_rejected",
    "재무/정산팀 인원을 줄이는 개편안을 기각했다",
    "정산 마감 지연 위험",
    ["현행 유지"],
)
REASON = "개발팀 인력 부족으로 기획팀 인원을 개발팀으로 이동"


class MockEmbedder:
    def __init__(self) -> None:
        self._mock = MockLLMClient()
        self.calls = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return await self._mock.embed(texts)


class BrokenEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("429 rate limited")


def _body(skill: str, inp: dict, token: str = "mock:hr", ctx: str = "ctx_orr_0001"):
    return {
        "meta": {
            "context_id": ctx,
            "token": token,
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            "idempotency_key": f"{ctx}:{skill}:{next(_keys)}",
        },
        "skill": skill,
        "input": inp,
    }


@pytest.fixture
def make(tmp_path: Path):
    made = []

    def _make(*, embedder=None, **settings):
        index = DenseIndex(str(tmp_path / "v.db"), embedder, "mock") if embedder else None
        svc = build_services(
            ArchiveSettings(mock=True, **settings),
            clock=ManualClock(),
            dense=DenseCardSearch(index) if index else None,
        )
        set_services(svc)
        made.append(svc)
        return svc, TestClient(app)

    yield _make
    set_services(None)
    for svc in made:
        svc.store.close()


def post(client, skill, inp, token="mock:hr") -> dict:
    return client.post("/a2a", json=_body(skill, inp, token)).json()


def record_card(client, n: int, spec: tuple) -> str:
    kind, summary, reason, *rest = spec if len(spec) > 3 else (*spec, [])
    rec = {
        "context_id": f"ctx_rec_{n:04d}",
        "kind": kind,
        "summary": summary,
        "decision_reason": reason,
        "alternatives_considered": rest[0] if rest else [],
        "source_refs": [f"req:{n}", "area:payment"],
        "author": "planning",
    }
    body = _body("record_case", rec, "mock:planning", ctx=rec["context_id"])
    res = client.post("/a2a", json=body).json()
    assert res["status"] == "completed", res
    return res["output"]["card_id"]


def find(client, reason=REASON, **extra) -> dict:
    return post(client, "find_similar_decisions", {"reason": reason, **extra})


def test_basis_finds_the_related_decision_card(make) -> None:
    embedder = MockEmbedder()
    _, client = make(embedder=embedder, dense_min_cosine=0.2)
    card = record_card(client, 1, STAFF)
    res = find(client)
    out = FindSimilarDecisionsOutput.model_validate(res["output"])
    assert embedder.calls > 0 and out.method == "dense"
    assert [c.card_id for c in out.candidates] == [card]
    assert 0.2 <= out.candidates[0].similarity <= 1.0 and out.candidates[0].rejected is False
    assert res["outcome"] == "found"


def test_basis_is_empty_when_nothing_is_close_enough(make) -> None:
    _, client = make(embedder=MockEmbedder(), dense_min_cosine=0.2)
    record_card(client, 1, STAFF)
    res = find(client, "회의실 예약 화면 색상 변경")
    assert res["outcome"] == "none" and res["output"]["candidates"] == []  # 근거 카드 없음


def test_basis_pool_is_confirmed_decision_cards_only(make) -> None:
    svc, client = make(embedder=MockEmbedder(), dense_min_cosine=0.1)
    incident = {
        "context_id": "ctx_inc_0001",
        "kind": "incident",
        "summary": "개발팀 인력 부족으로 장애 대응이 늦어졌다",
        "decision_reason": "개발팀 인력 부족",
        "alternatives_considered": ["현행 유지"],
        "source_refs": ["inc:1"],
        "author": "orchestrator",
    }
    body = _body("record_case", incident, "mock:orchestrator", ctx="ctx_inc_0001")
    assert client.post("/a2a", json=body).json()["status"] == "completed"
    candidate_card = {
        "context_id": "ctx_ext_0001",
        "kind": "decision_extracted",
        "summary": "개발팀 인력 부족으로 인원을 이동하기로 했다",
        "source_refs": ["doc:1"],
        "author": "planning",
    }
    body = _body("record_case", candidate_card, "mock:planning", ctx="ctx_ext_0001")
    assert client.post("/a2a", json=body).json()["status"] == "completed"
    # 장애 대응 카드와 확정 전 후보 카드는 근거 후보가 아니다 (둘 다 카드로 만들어져 있다)
    active = asyncio.run(svc.memory.list_cards(area=None, states=["active"]))
    assert [c.kind for c in active] == ["incident"]
    assert len(asyncio.run(svc.memory.list_cards(area=None, states=["candidate"]))) == 1
    assert find(client)["output"]["candidates"] == []


def test_basis_marks_a_rejected_past_decision(make) -> None:
    _, client = make(embedder=MockEmbedder(), dense_min_cosine=0.2)
    card = record_card(client, 1, REJECTED)
    res = find(client, "재무/정산팀 인원을 줄이는 개편")
    assert [(c["card_id"], c["rejected"]) for c in res["output"]["candidates"]] == [(card, True)]


def test_basis_falls_back_to_dice_when_embedding_fails(make) -> None:
    _, client = make(embedder=BrokenEmbedder())
    card = record_card(client, 1, STAFF)
    out = FindSimilarDecisionsOutput.model_validate(find(client)["output"])
    assert out.method == "dice" and [c.card_id for c in out.candidates] == [card]


def test_basis_limit_and_input_are_checked(make) -> None:
    _, client = make(embedder=MockEmbedder(), dense_min_cosine=0.0)
    for n in range(1, 5):
        record_card(client, n, (STAFF[0], f"{STAFF[1]} {n}", STAFF[2], STAFF[3]))
    assert len(find(client, limit=2)["output"]["candidates"]) == 2
    assert find(client, "가")["outcome"] == "INVALID_INPUT"
    assert find(client, limit=9)["outcome"] == "INVALID_INPUT"


def test_finding_decisions_writes_nothing(make) -> None:
    svc, client = make(embedder=MockEmbedder(), dense_min_cosine=0.2)
    record_card(client, 1, STAFF)
    before = svc.store.all("SELECT * FROM mock_cards")
    find(client)
    assert svc.store.all("SELECT * FROM mock_cards") == before


def test_only_hr_may_ask_for_decisions(make) -> None:
    _, client = make(embedder=MockEmbedder(), dense_min_cosine=0.2)
    for token in ("mock:orchestrator", "mock:finance", "mock:stranger"):
        res = post(client, "find_similar_decisions", {"reason": REASON}, token)
        assert (res["status"], res["outcome"]) == ("rejected", "FORBIDDEN_SKILL")
    ok = post(client, "find_similar_decisions", {"reason": REASON}, "mock:hr")
    assert ok["status"] == "completed"
    assert FindSimilarDecisionsOutput.model_validate(ok["output"]).method in ("dense", "dice")
