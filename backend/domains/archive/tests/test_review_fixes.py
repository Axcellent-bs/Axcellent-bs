"""독립 리뷰에서 나온 버그의 회귀 테스트(아카이브 쪽): 경과 일수 날짜 계산, 카드 작성자."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from archive_agent.main import app
from archive_agent.planning_check.service import _decided_at
from ax_contracts.schemas import Card


def body(skill, inp, key="k", ctx="ctx_rev_0001", token="mock:orchestrator"):
    return {
        "meta": {
            "context_id": ctx,
            "token": token,
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            "idempotency_key": key,
        },
        "skill": skill,
        "input": inp,
    }


@pytest.fixture
def client(svc) -> TestClient:
    return TestClient(app)


def post(client, skill, inp, key="k", ctx="ctx_rev_0001", token="mock:orchestrator"):
    return client.post("/a2a", json=body(skill, inp, key, ctx, token)).json()


# 10) 경과 일수는 UTC 날짜로 센다 --------------------------------------------------------
def test_days_since_rejected_uses_utc_dates() -> None:
    kst = datetime(2026, 10, 22, 8, 0, tzinfo=timezone(timedelta(hours=9)))
    card = Card(
        card_id="card_x",
        kind="decision",
        title="t",
        body="b",
        context_id="ctx_x_0001",
        author="a",
        confidence="high",
        created_at=kst,
        extra={"decided_at": kst.isoformat()},
    )
    assert _decided_at(card).date().isoformat() == "2026-10-21"  # UTC로는 전날 23시


# 요청 본문의 author가 아니라 토큰의 요청자가 카드 작성자다 -----------------------------
def test_card_author_is_the_verified_requester(svc, client) -> None:
    rec = {
        "context_id": "ctx_au_0001",
        "kind": "spec_confirmed",
        "summary": "확정",
        "decision_reason": "이유",
        "alternatives_considered": ["대안"],
        "source_refs": ["pr:1", "area:payment"],
        "author": "finance",
    }
    res = post(client, "record_case", rec, "r", "ctx_au_0001", "mock:planning")
    row = svc.store.one(
        "SELECT data FROM mock_cards WHERE card_id = ?", (res["output"]["card_id"],)
    )
    assert '"author": "planning"' in row["data"] or '"author":"planning"' in row["data"]
