"""개발(dev) 쪽 dev_handoff 시험 (분리 5단계: 기획 시험에서 나눔)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dev_agent.main import app
from planning_agent.dev_handoff import START_DEV


@pytest.mark.role("dev")
def test_start_dev_handler_needs_key_and_acl(a2a_body) -> None:
    client = TestClient(app)
    payload = {"request": {}, "spec": {}, "tasks": []}
    no_key = client.post("/a2a", json=a2a_body(START_DEV, payload, token="mock:planning")).json()
    assert (no_key["status"], no_key["outcome"]) == ("failed", "missing_idempotency_key")
    denied = client.post(
        "/a2a", json=a2a_body(START_DEV, payload, token="mock:support", idempotency_key="k")
    ).json()
    assert (denied["status"], denied["outcome"]) == ("rejected", "FORBIDDEN_SKILL")
    bad = client.post(
        "/a2a", json=a2a_body(START_DEV, payload, token="mock:planning", idempotency_key="k")
    ).json()
    assert (bad["status"], bad["outcome"]) == ("failed", "invalid_input")
