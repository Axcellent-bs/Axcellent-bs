"""root_cause_hypotheses: 코드가 모은 사실로만 원인 후보를 고른다.

- 이상 직전 배포가 있으면 그게 1순위 후보
- 근거 없는 후보·지어낸 숫자는 버려짐
- 사실 0개 → 확인할 수 없음
- 서비스 꺼짐 → 건너뛰고 계속
- LLM 실패 → 시간 순서 규칙으로 대체 (fallback)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template.llm import LLMResponseError
from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from ax_contracts.schemas.data import RootCauseInput
from data_agent import handlers
from data_agent.clients import fact_sources
from data_agent.main import build_app
from data_agent.skills.root_cause import _Candidate, _Candidates, estimate_root_cause

CTX = "ctx_inc_rootcause"
START = datetime(2026, 10, 29, 10, 0, tzinfo=UTC)
END = START + timedelta(minutes=20)
NOW = END + timedelta(minutes=5)
DATA = RootCauseInput(
    context_id=CTX,
    window_from=START,
    window_to=END,
    metric="payment_fail_rate",
    component="payment",
)


def _meta() -> RequestMeta:
    return RequestMeta(
        context_id=CTX,
        token="mock:orchestrator",
        path=["orchestrator"],
        deadline=datetime.now(UTC) + timedelta(seconds=30),
    )


def _iso(at: datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


PR_BEFORE = {
    "pr_number": 412,
    "title": "결제 재시도 설정 변경",
    "merged_at": _iso(START - timedelta(minutes=10)),
}
PR_OLDER = {
    "pr_number": 405,
    "title": "문구 수정",
    "merged_at": _iso(START - timedelta(minutes=25)),
}
NOTICE = {
    "found": True,
    "notice_id": "ntc_7",
    "channel_code": "banner",
    "context_id": "ctx_mkt_campaign",
    "published_at": _iso(START - timedelta(minutes=15)),
}
OWN_NOTICE = {**NOTICE, "notice_id": "ntc_8", "context_id": CTX}  # 이 사건 대응 공지 → 사실 아님
CARD = {
    "card": {
        "card_id": "card_old1",
        "context_id": "ctx_inc_old",
        "title": "결제 게이트웨이 시간 초과",
    },
    "score": 0.8,
    "low_confidence": False,
}


def _ok(output: dict[str, Any]):
    return lambda req: AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output=output)


def _disabled(service: str):
    return lambda req: AgentResult(
        meta=req.meta,
        status=TaskStatus.REJECTED,
        outcome="service_disabled",
        output={"to_service": service},
    )


def sources(**override) -> dict[str, Any]:
    base = {
        "find_related_prs": _ok({"candidates": [PR_OLDER, PR_BEFORE]}),
        "list_active_notices": _ok({"items": [NOTICE, OWN_NOTICE], "total": 2}),
        "detect_inquiry_surge": _ok({"evaluated_at": _iso(NOW), "surges": []}),
        "card_search": _ok({"hits": [CARD]}),
    }
    return {**base, **override}


class Sender:
    """skill별 가짜 응답. 받은 (대상, skill)을 기록한다."""

    def __init__(self, table: dict[str, Any]):
        self.table = table
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, target: str, req: AgentRequest) -> AgentResult:
        self.calls.append((target, req.skill))
        return self.table[req.skill](req)


class FakeLLM:
    def __init__(self, candidates: list[dict[str, Any]] | None = None, fail: bool = False):
        self.candidates = candidates or []
        self.fail = fail
        self.seen: dict[str, Any] | None = None

    async def structured(self, prompt, schema, untrusted=None, **_):  # noqa: ANN001
        self.seen = untrusted
        if self.fail:
            raise LLMResponseError("LLM 응답 없음")
        assert schema is _Candidates
        return _Candidates(hypotheses=[_Candidate(**c) for c in self.candidates])


def _fact_id(out, ref: str) -> str:
    return next(f.fact_id for f in out.facts if f.ref == ref)


async def _run(table: dict[str, Any], llm: FakeLLM):
    sender = Sender(table)
    result = await estimate_root_cause(DATA, _meta(), llm, send=sender, now=NOW)
    return result, sender


@pytest.mark.asyncio
async def test_facts_come_from_four_sources_over_a2a() -> None:
    result, sender = await _run(sources(), FakeLLM(fail=True))
    assert sorted(sender.calls) == [
        ("marketing", "list_active_notices"),
        ("memory", "card_search"),
        ("planning", "find_related_prs"),
        ("support", "detect_inquiry_surge"),
    ]
    kinds = [f.kind for f in result.output.facts]
    assert kinds.count("deploy") == 2 and "notice" in kinds and "past_incident" in kinds
    refs = {f.ref for f in result.output.facts}
    assert "ntc_8" not in refs  # 이 사건에 대응해 올린 공지는 원인 사실이 아니다
    deploy = next(f for f in result.output.facts if f.ref == "pr:412")
    assert deploy.at == START - timedelta(minutes=10)
    assert deploy.source == "planning.find_related_prs"


@pytest.mark.asyncio
async def test_deploy_right_before_the_anomaly_is_the_first_candidate() -> None:
    result, _ = await _run(sources(), FakeLLM(fail=True))
    out = result.output
    first = out.hypotheses[0]
    assert _fact_id(out, "pr:412") in first.fact_ids  # 직전 배포(10분 전)가 25분 전 배포보다 앞
    assert "PR #412" in first.explanation
    assert first.confidence == "mid"
    assert out.needs_confirmation is True  # 규칙만으로는 high가 없다


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_time_order_rule() -> None:
    result, _ = await _run(sources(), FakeLLM(fail=True))
    assert result.fallback is True
    assert result.output.hypotheses
    assert "규칙" in result.output.summary


@pytest.mark.asyncio
async def test_llm_gets_only_facts_and_a_grounded_high_candidate_is_kept() -> None:
    llm = FakeLLM()
    sender = Sender(sources())
    # 사실 id는 순서대로 붙는다: f1 이상 구간, f2·f3 배포 …
    llm.candidates = [
        {
            "explanation": "10:00 이상 10분 전 PR #412 배포가 원인일 수 있음",
            "fact_ids": ["f3", "f1"],
            "confidence": "high",
        }
    ]
    result = await estimate_root_cause(DATA, _meta(), llm, send=sender, now=NOW)
    out = result.output
    assert llm.seen is not None
    assert [f["fact_id"] for f in llm.seen["facts"]] == [f.fact_id for f in out.facts]
    assert out.facts[2].ref == "pr:412"
    assert result.fallback is False
    assert [h.confidence for h in out.hypotheses] == ["high"]
    assert out.needs_confirmation is False


@pytest.mark.asyncio
async def test_ungrounded_candidates_and_made_up_numbers_are_dropped() -> None:
    llm = FakeLLM(
        [
            {"explanation": "DB 장애일 수 있음", "fact_ids": ["f99"], "confidence": "high"},
            {
                "explanation": "결제 실패율이 35%로 올랐을 수 있음",
                "fact_ids": ["f3"],
                "confidence": "high",
            },
            {
                "explanation": "11:47 배포가 원인일 수 있음",
                "fact_ids": ["f3"],
                "confidence": "high",
            },
            {
                "explanation": "이상 구간 자체가 원인일 수 있음",
                "fact_ids": ["f1"],
                "confidence": "mid",
            },
            {"explanation": "근거 없이 고른 후보", "fact_ids": [], "confidence": "low"},
            {
                "explanation": "PR #412 배포 뒤 이상이 생겼을 수 있음",
                "fact_ids": ["f3"],
                "confidence": "mid",
            },
        ]
    )
    result, _ = await _run(sources(), llm)
    out = result.output
    assert [h.explanation for h in out.hypotheses] == ["PR #412 배포 뒤 이상이 생겼을 수 있음"]
    assert result.fallback is False
    assert out.needs_confirmation is True  # 남은 후보에 high가 없다


@pytest.mark.asyncio
async def test_all_candidates_dropped_uses_the_rule() -> None:
    llm = FakeLLM([{"explanation": "실패 1,234건", "fact_ids": ["f3"], "confidence": "high"}])
    result, _ = await _run(sources(), llm)
    assert result.fallback is True
    assert "PR #412" in result.output.hypotheses[0].explanation


@pytest.mark.asyncio
async def test_no_facts_means_cannot_tell_without_candidates() -> None:
    empty = sources(
        find_related_prs=_ok({"candidates": []}),
        list_active_notices=_ok({"items": []}),
        card_search=_ok({"hits": []}),
    )
    llm = FakeLLM([{"explanation": "아무거나", "fact_ids": ["f1"], "confidence": "high"}])
    result, _ = await _run(empty, llm)
    out = result.output
    assert out.hypotheses == []
    assert out.needs_confirmation is True
    assert out.summary.startswith("확인할 수 없음")
    assert llm.seen is None  # 사실이 없으면 LLM을 부르지 않는다
    assert [f.kind for f in out.facts] == ["anomaly"]


@pytest.mark.asyncio
async def test_disabled_service_is_skipped_and_the_rest_continues() -> None:
    table = sources(find_related_prs=_disabled("dev"), detect_inquiry_surge=_disabled("inquiry"))
    result, _ = await _run(table, FakeLLM(fail=True))
    out = result.output
    off = [f for f in out.facts if f.kind == "unavailable"]
    assert len(off) == 2
    assert all(f.summary.startswith("확인할 수 없음(서비스 꺼짐)") for f in off)
    assert any("dev 서비스" in f.summary for f in off)
    assert all(f.at is None for f in off)
    # 배포는 못 봤지만 공지·지난 사건으로 계속한다
    assert {f.kind for f in out.facts} >= {"notice", "past_incident"}
    assert out.hypotheses
    unavailable_ids = {f.fact_id for f in off}
    assert not any(set(h.fact_ids) & unavailable_ids for h in out.hypotheses)


@pytest.mark.asyncio
async def test_a_failing_source_is_recorded_not_invented() -> None:
    async def boom(target: str, req: AgentRequest) -> AgentResult:
        if req.skill == "card_search":
            raise RuntimeError("down")
        return sources()[req.skill](req)

    result = await estimate_root_cause(DATA, _meta(), FakeLLM(fail=True), send=boom, now=NOW)
    off = [f for f in result.output.facts if f.kind == "unavailable"]
    assert [f.source for f in off] == ["memory.card_search"]
    assert off[0].summary.startswith("확인할 수 없음")


# ---- A2A 서버로 부르기


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setattr(handlers, "get_llm", lambda: FakeLLM(fail=True))
    monkeypatch.setattr(fact_sources, "call_agent", Sender(sources()))
    return TestClient(build_app())


def _body(skill: str, payload: dict) -> dict:
    return {
        "meta": {
            "context_id": CTX,
            "token": "mock:orchestrator",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": skill,
        "input": payload,
    }


def test_skill_answers_over_a2a(client: TestClient) -> None:
    payload = json.loads(DATA.model_dump_json())
    result = client.post("/a2a", json=_body("root_cause_hypotheses", payload)).json()
    assert result["status"] == "completed", result
    assert result["fallback"] is True
    assert result["output"]["hypotheses"][0]["fact_ids"]
    assert "planning.find_related_prs" in result["evidence"]["queries"]


def test_reversed_window_is_invalid(client: TestClient) -> None:
    payload = {**json.loads(DATA.model_dump_json()), "window_to": _iso(START - timedelta(hours=1))}
    result = client.post("/a2a", json=_body("root_cause_hypotheses", payload)).json()
    assert result["status"] == "failed"
    assert result["outcome"] == "invalid_input"


@pytest.mark.asyncio
async def test_same_confidence_is_reordered_by_proximity_not_llm_order() -> None:
    """LLM이 오래된 배포(25분 전)를 먼저 줘도 이상 시작과 가까운 배포(10분 전)가 1순위."""
    llm = FakeLLM(
        [
            {
                "explanation": "PR #405 배포 뒤 이상일 수 있음",
                "fact_ids": ["f2"],
                "confidence": "mid",
            },
            {
                "explanation": "PR #412 배포 뒤 이상일 수 있음",
                "fact_ids": ["f3"],
                "confidence": "mid",
            },
        ]
    )
    result, _ = await _run(sources(), llm)
    out = result.output
    assert out.facts[1].ref == "pr:405" and out.facts[2].ref == "pr:412"
    assert [h.fact_ids for h in out.hypotheses] == [["f3"], ["f2"]]
    assert result.fallback is False
    assert (
        "같은 확신도 안에서는 근거 사실 시각이 이상 시작과 가까운 순"
        in result.evidence.data_summary
    )


@pytest.mark.asyncio
async def test_higher_confidence_still_comes_first() -> None:
    llm = FakeLLM(
        [
            {
                "explanation": "PR #412 배포 뒤 이상일 수 있음",
                "fact_ids": ["f3"],
                "confidence": "low",
            },
            {
                "explanation": "PR #405 배포 뒤 이상일 수 있음",
                "fact_ids": ["f2"],
                "confidence": "high",
            },
        ]
    )
    result, _ = await _run(sources(), llm)
    assert [h.confidence for h in result.output.hypotheses] == ["high", "low"]
