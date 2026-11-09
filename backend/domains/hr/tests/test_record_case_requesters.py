"""개발(dev)도 인사 record_case를 부를 수 있다 (dev 서비스 분리 #881 뒤 PR 머지·요청 완료 기록)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hr_agent.main import app

BACKEND = Path(__file__).resolve().parents[3]
CARDS = [
    BACKEND / "domains" / "hr" / "agent-card.json",
    BACKEND / "services" / "decisions" / "agent-card.json",
]


def _call(client: TestClient, token: str, ctx: str) -> dict:
    record = {
        "context_id": ctx,
        "kind": "decision",
        "summary": "PR #12 머지: 결제 타임아웃 조정",
        "area": "payment",
        "author": "dev",
    }
    return client.post(
        "/a2a",
        json={
            "meta": {
                "context_id": ctx,
                "token": token,
                "path": [],
                "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
                "idempotency_key": f"{ctx}:rc",
            },
            "skill": "record_case",
            "input": record,
        },
    ).json()


@pytest.mark.parametrize("card", CARDS, ids=lambda p: p.parent.name)
def test_cards_let_dev_record_cases(card: Path) -> None:
    skills = json.loads(card.read_text("utf-8"))["skills"]
    skill = next(s for s in skills if s["id"] == "record_case")
    assert {"dev", "planning"} <= set(skill["allowed_requesters"])


def test_dev_can_call_record_case_and_strangers_cannot(svc) -> None:
    client = TestClient(app)
    res = _call(client, "mock:dev", "ctx_dev_0001")
    assert res["status"] == "completed", res
    stranger = _call(client, "mock:stranger", "ctx_dev_0002")
    assert stranger["outcome"] == "FORBIDDEN_SKILL"
