"""계획 비교: 임베딩 후보 검색, Dice 대체, LLM 설명과 그 검증."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas import CheckPlanOutput
from hr_agent.config import HrSettings, ManualClock
from hr_agent.main import app
from hr_agent.narrative import Narrator
from hr_agent.retrieval.cards import DenseCardSearch
from hr_agent.retrieval.dense import DenseIndex
from hr_agent.services import build_services, set_services

REJECTED = ("decision_rejected", "결제 서버 캐시 도입안을 기각했다", "정합성 위험", ["현행 유지"])
ADOPTED = ("spec_confirmed", "결제 재시도를 최대 2회로 확정했다", "중복 결제 위험", ["재시도 없음"])


class MockEmbedder:
    """글자(토큰) 기반 목 임베딩. 호출 횟수를 센다."""

    def __init__(self) -> None:
        self.calls = 0
        self._mock = MockLLMClient()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return await self._mock.embed(texts)


class BrokenEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("429 rate limited")


class FakeLLM:
    """설명 문장을 정해진 대로 돌려주거나 실패한다."""

    def __init__(self, text: str | None = None, error: Exception | None = None) -> None:
        self.text, self.error = text, error
        self.untrusted: list[dict | None] = []

    async def structured(self, prompt, schema, untrusted=None, **kwargs):
        self.untrusted.append(untrusted)
        if self.error:
            raise self.error
        return schema(text=self.text)


def _body(skill: str, inp: dict, key: str, ctx: str = "ctx_pln_0001") -> dict:
    return {
        "meta": {
            "context_id": ctx,
            "token": "mock:planning",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            "idempotency_key": f"{ctx}:{key}",
        },
        "skill": skill,
        "input": inp,
    }


@pytest.fixture
def make(tmp_path):
    made = []

    def _make(*, embedder=None, llm=None, **settings):
        clock = ManualClock()
        index = DenseIndex(str(tmp_path / "v.db"), embedder, "mock") if embedder else None
        services = build_services(
            HrSettings(mock=True, **settings),
            clock=clock,
            llm=llm,
            dense=DenseCardSearch(index) if index else None,
        )
        set_services(services)
        made.append(services)
        return services, TestClient(app)

    yield _make
    set_services(None)
    for s in made:
        s.store.close()


def _record(client, n: int, spec: tuple, area: str = "payment") -> str:
    kind, summary, reason, alts = spec
    rec = {
        "context_id": f"ctx_rec_{n:04d}",
        "kind": kind,
        "summary": summary,
        "decision_reason": reason,
        "alternatives_considered": alts,
        "source_refs": [f"req:{n}", f"area:{area}"],
        "author": "planning",
    }
    res = client.post("/a2a", json=_body("record_case", rec, f"r{n}", ctx=rec["context_id"])).json()
    assert res["status"] == "completed", res
    return res["output"]["card_id"]


def _check(client, title: str, summary: str, area: str = "payment") -> CheckPlanOutput:
    draft = {"title": title, "summary": summary, "area": area, "kind": "spec"}
    res = client.post("/a2a", json=_body("check_plan", {"draft": draft}, "chk")).json()
    return CheckPlanOutput.model_validate(res["output"])


CACHE = ("결제 서버 캐시 도입", "결제 응답 속도를 위해 서버 캐시를 도입한다")
RETRY = ("결제 재시도를 최대 2회로", "결제 재시도를 최대 2회로 제한한다")


def test_dense_search_finds_the_related_card(make) -> None:
    embedder = MockEmbedder()
    svc, client = make(embedder=embedder, dense_min_cosine=0.2)
    card = _record(client, 1, REJECTED)
    out = _check(client, *CACHE)
    assert embedder.calls > 0  # Dense 경로를 탔다
    assert [c.card_id for c in out.similar_cards] == [card]
    assert [f.code for f in out.findings] == ["REJECTED_BEFORE"]


def test_dense_min_cosine_hides_unrelated_cards(make) -> None:
    svc, client = make(embedder=MockEmbedder(), dense_min_cosine=0.2)
    _record(client, 1, REJECTED)
    out = _check(client, "회의실 예약 화면 개편", "예약 화면 색상을 바꾼다")
    assert out.no_related_cards is True and out.findings == []


def test_embedding_failure_falls_back_to_dice(make) -> None:
    svc, client = make(embedder=BrokenEmbedder(), dense_min_cosine=0.2)
    card = _record(client, 1, REJECTED)
    out = _check(client, *CACHE)  # 임베딩이 실패해도 Dice로 같은 판정이 나온다
    assert [f.code for f in out.findings] == ["REJECTED_BEFORE"]
    assert out.findings[0].card_ids == [card]


def test_match_needs_the_dense_match_cosine(make) -> None:
    svc, client = make(embedder=MockEmbedder(), dense_min_cosine=0.2, dense_match_cosine=0.99)
    _record(client, 1, ADOPTED)
    assert _check(client, *RETRY).findings == []  # 관련은 있으나 같은 방향 기준에는 못 미친다
    svc2, client2 = make(embedder=MockEmbedder(), dense_min_cosine=0.2, dense_match_cosine=0.3)
    _record(client2, 1, ADOPTED)
    assert [f.code for f in _check(client2, *RETRY).findings] == ["MATCH"]


def test_llm_explanation_is_attached_when_numbers_match(make) -> None:
    llm = FakeLLM("12일 전에 기각된 캐시 도입안과 같은 방향이라 같은 이유로 다시 검토가 필요하다.")
    svc, client = make(embedder=MockEmbedder(), llm=llm, dense_min_cosine=0.2)
    svc.narrator = Narrator(llm)
    _record(client, 1, REJECTED)
    svc.clock.advance(days=12)
    out = _check(client, *CACHE)
    finding = out.findings[0]
    assert finding.explanation and "12일" in finding.explanation
    assert "12일" in finding.message  # 고정 문장은 그대로다
    assert llm.untrusted[-1] == {"계획 요약": CACHE[1]}  # 사용자 글은 데이터 블록으로만 간다


def test_explanation_with_an_invented_number_is_dropped(make) -> None:
    llm = FakeLLM("99일 전에 기각된 안이라 위험하다.")
    svc, client = make(embedder=MockEmbedder(), llm=llm, dense_min_cosine=0.2)
    svc.narrator = Narrator(llm)
    _record(client, 1, REJECTED)
    svc.clock.advance(days=12)
    finding = _check(client, *CACHE).findings[0]
    assert finding.explanation is None and "12일" in finding.message


@pytest.mark.parametrize(
    "text",
    ["이 계획은 그대로 진행해도 된다.", "MATCH 판정이라 같은 방향이다.", "추진하기 어렵습니다."],
)
def test_explanation_that_judges_is_dropped(make, text) -> None:
    llm = FakeLLM(text)
    svc, client = make(embedder=MockEmbedder(), llm=llm, dense_min_cosine=0.2)
    svc.narrator = Narrator(llm)
    _record(client, 1, REJECTED)
    assert _check(client, *CACHE).findings[0].explanation is None


def test_llm_failure_keeps_the_fixed_message(make) -> None:
    llm = FakeLLM(error=RuntimeError("timeout"))
    svc, client = make(embedder=MockEmbedder(), llm=llm, dense_min_cosine=0.2)
    svc.narrator = Narrator(llm)
    _record(client, 1, REJECTED)
    out = _check(client, *CACHE)
    assert out.verdict == "warn" and out.findings[0].explanation is None


def test_without_llm_there_is_no_explanation(make) -> None:
    svc, client = make(embedder=MockEmbedder(), dense_min_cosine=0.2)
    _record(client, 1, REJECTED)
    assert _check(client, *CACHE).findings[0].explanation is None
