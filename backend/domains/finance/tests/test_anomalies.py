"""재무 이상 건 목록: 찾은 의심 건은 사람이 처리할 때까지 남고, 처리하면 열린 목록에서 빠진다."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import EventEnvelope
from finance_agent import anomalies, chat_log, handlers
from finance_agent.main import app
from finance_agent.sources import MockSources

HUMAN = {"Authorization": "Bearer human:taeyoon"}
FINDING = {
    "code": "repeat_refund",
    "severity": "medium",
    "title": "같은 고객 반복 환불",
    "detail": "buy_0008의 주문 3건 환불",
    "refs": ["ord_1", "ord_2"],
}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.delenv("FINANCE_HUMAN_TOKENS", raising=False)
    monkeypatch.delenv("SAFETY_HUMAN_TOKENS", raising=False)
    handlers.reset_state(MockSources())


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> list[EventEnvelope]:
    sent: list[EventEnvelope] = []

    async def fake(envelope: EventEnvelope) -> None:
        sent.append(envelope)

    monkeypatch.setattr(chat_log.events, "publish", fake)
    monkeypatch.setattr(chat_log, "_seen_findings", set())
    return sent


def _store() -> dict[str, dict[str, Any]]:
    return handlers.state().records.anomalies


@pytest.mark.asyncio
async def test_a_finding_stays_open_until_a_person_resolves_it(published) -> None:
    await handlers.note_audit([FINDING], "shop-demo")
    await handlers.note_audit([], "shop-demo")  # 감사 기간이 지나 다시 안 나와도
    listing = anomalies.listing(_store())
    assert listing["open_count"] == 1
    ref = listing["open"][0]["finding_ref"]
    assert ref == anomalies.signature(FINDING) and len(published) == 1

    anomalies.resolve(_store(), ref, "taeyoon", "고객에게 확인함")
    await handlers.note_audit([FINDING], "shop-demo")  # 같은 건이 다시 나와도 다시 열지 않는다
    listing = anomalies.listing(_store())
    assert listing["open_count"] == 0
    assert listing["resolved"][0]["resolved_by"] == "taeyoon"
    assert len(published) == 1


@pytest.mark.asyncio
async def test_a_failed_announcement_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_log, "_seen_findings", set())
    sent: list[EventEnvelope] = []

    async def broken(envelope: EventEnvelope) -> None:
        raise RuntimeError("버스 끊김")

    monkeypatch.setattr(chat_log.events, "publish", broken)
    await handlers.note_audit([FINDING], "shop-demo")
    assert _store()[anomalies.signature(FINDING)]["announced"] is False

    async def fine(envelope: EventEnvelope) -> None:
        sent.append(envelope)

    monkeypatch.setattr(chat_log.events, "publish", fine)
    await handlers.note_audit([FINDING], "shop-demo")
    assert len(sent) == 1 and _store()[anomalies.signature(FINDING)]["announced"] is True


def test_open_items_come_most_severe_first() -> None:
    store: dict[str, dict[str, Any]] = {}
    low = FINDING | {"severity": "low", "refs": ["a"]}
    high = FINDING | {"severity": "high", "refs": ["b"]}
    anomalies.note_findings(store, [low, high])
    assert [e["severity"] for e in anomalies.listing(store)["open"]] == ["high", "low"]


def test_api_lists_and_resolves_with_a_human_token(published) -> None:
    client = TestClient(app)
    anomalies.note_findings(_store(), [FINDING])
    ref = anomalies.signature(FINDING)
    body = client.get("/api/finance/anomalies").json()
    assert body["data"]["open_count"] == 1 and "announced" not in body["data"]["open"][0]

    url = f"/api/finance/anomalies/{ref}/resolve"
    assert client.post(url, json={"note": "확인"}).status_code == 401
    assert client.post(url, json={"note": " "}, headers=HUMAN).status_code == 400
    assert (
        client.post(
            "/api/finance/anomalies/nope/resolve", json={"note": "x"}, headers=HUMAN
        ).status_code
        == 404
    )
    done = client.post(url, json={"note": "판매자에게 확인함"}, headers=HUMAN).json()
    assert done["data"]["status"] == "resolved" and done["data"]["note"] == "판매자에게 확인함"
    assert client.get("/api/finance/anomalies").json()["data"]["open_count"] == 0


def test_resolve_path_allows_post_from_the_dashboard() -> None:
    from finance_agent.main import DECISION_PATHS

    assert DECISION_PATHS.match("/api/finance/anomalies/abc/resolve")
    assert not DECISION_PATHS.match("/api/finance/anomalies")


def test_skill_reads_an_announced_finding_after_the_audit_window(published) -> None:
    """총괄이 의심 건 번호로 물으면 감사 기간 밖이어도 처리 전 기록을 돌려준다."""
    anomalies.note_findings(_store(), [FINDING])  # 시드 장부의 최근 7일에는 없는 건
    body = {
        "meta": {
            "context_id": "ctx_fin_1",
            "token": "mock:orchestrator",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": "audit_report",
        "input": {"days": 7, "finding_ref": anomalies.signature(FINDING)},
        "untrusted": {},
    }
    result = TestClient(app).post("/a2a", json=body).json()
    assert result["status"] == "completed"
    assert result["output"]["findings"] == [
        anomalies.finding_view(_store()[anomalies.signature(FINDING)])
    ]
