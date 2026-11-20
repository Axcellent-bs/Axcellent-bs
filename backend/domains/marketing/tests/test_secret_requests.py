"""비밀 저장소 요청 적용 스킬과 키 소비자: 관문 params에 값 없음, 승인 뒤 적용, db 모드 키 읽기."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ax_contracts.schemas import AgentRequest, ExecuteDecision, HoldReason, RequestMeta
from ax_secret_store import resolver
from ax_secret_store.crypto import generate_master_key
from ax_secret_store.service import SecretService
from marketing_agent import handlers, secret_requests
from marketing_agent.media.config import has_api_key

DUMMY = "DUMMY-NOT-A-REAL-KEY-0001"
DUMMY2 = "DUMMY-NOT-A-REAL-KEY-0002"
NAME = "MARKETING_VIDEO_API_KEY"


class Gate:
    def __init__(self, status: str) -> None:
        self.status = status
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, action: str, params: dict, meta: RequestMeta, evidence: Any):
        self.calls.append((action, params))
        held = self.status == "held"
        extra = {"review_id": "rev_9", "hold_reason": HoldReason.OVER_THRESHOLD} if held else {}
        return ExecuteDecision(
            execution_id="exe_1", grade="C" if held else "A", status=self.status, **extra
        )


@pytest.fixture(autouse=True)
def store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SecretService:
    monkeypatch.setenv("AX_SECRET_STORE", "db")
    monkeypatch.setenv("AX_SECRET_MASTER_KEY", generate_master_key())
    monkeypatch.setenv("AX_SECRET_DB_FILE", str(tmp_path / "s.db"))
    monkeypatch.setenv("AX_SECRET_CACHE_TTL", "0")
    monkeypatch.setenv("AX_SECRET_DEFAULT_TENANT", "acme")
    monkeypatch.delenv(NAME, raising=False)
    resolver.reset_for_tests()
    yield SecretService.from_env()
    resolver.reset_for_tests()


def _req(request_id: str) -> AgentRequest:
    meta = RequestMeta(
        context_id="ctx_s",
        token="mock:dashboard-api",
        deadline=datetime.now(UTC) + timedelta(seconds=30),
    )
    return AgentRequest(
        meta=meta, skill="x", input={"request_id": request_id, "updated_by": "민지"}
    )


def _gate(monkeypatch: pytest.MonkeyPatch, status: str) -> Gate:
    gate = Gate(status)
    monkeypatch.setattr(secret_requests, "execute", gate.execute)
    return gate


@pytest.mark.asyncio
async def test_add_applies_and_gate_params_carry_no_value(
    monkeypatch: pytest.MonkeyPatch, store: SecretService
) -> None:
    gate = _gate(monkeypatch, "executed")
    req = store.stage("add", "acme", NAME, DUMMY, "민지")
    out = await handlers.add_secret_skill(_req(req["id"]))
    assert out.outcome == "applied"
    assert gate.calls[0][0] == "add_secret"
    sent = json.dumps(gate.calls[0][1]) + out.model_dump_json()
    assert DUMMY not in sent and "ciphertext" not in sent
    assert gate.calls[0][1]["fingerprint"] and gate.calls[0][1]["hint"] == DUMMY[-4:]
    assert resolver.get_secret(NAME) == DUMMY
    assert has_api_key("video")


@pytest.mark.asyncio
async def test_replace_is_held_then_applies_after_approval(
    monkeypatch: pytest.MonkeyPatch, store: SecretService
) -> None:
    store.apply(store.stage("add", "acme", NAME, DUMMY, "민지")["id"], "민지")
    req = store.stage("replace", "acme", NAME, DUMMY2, "민지")
    _gate(monkeypatch, "held")
    held = await handlers.change_secret_skill(_req(req["id"]))
    assert held.outcome == "held"
    assert store.pending(req["id"]).review_id == "rev_9"
    assert resolver.get_secret(NAME) == DUMMY
    gate = _gate(monkeypatch, "executed")  # 승인됨 → 같은 요청을 다시 보내면 적용
    done = await handlers.change_secret_skill(_req(req["id"]))
    assert done.outcome == "applied"
    assert gate.calls[0][1]["requested_by"] == "민지"
    assert resolver.get_secret(NAME) == DUMMY2
    assert DUMMY2 not in done.model_dump_json()


@pytest.mark.asyncio
async def test_skill_rejects_wrong_kind_missing_request_and_env_mode(
    monkeypatch: pytest.MonkeyPatch, store: SecretService
) -> None:
    gate = _gate(monkeypatch, "executed")
    req = store.stage("add", "acme", NAME, DUMMY, "민지")
    assert (await handlers.change_secret_skill(_req(req["id"]))).outcome == "request_mismatch"
    assert (await handlers.add_secret_skill(_req("sr-none"))).outcome == "request_not_found"
    assert gate.calls == []
    monkeypatch.setenv("AX_SECRET_STORE", "env")
    assert (await handlers.add_secret_skill(_req(req["id"]))).outcome == "store_disabled"


def test_consumers_use_env_in_env_mode_and_db_in_db_mode(
    monkeypatch: pytest.MonkeyPatch, store: SecretService
) -> None:
    store.apply(store.stage("add", "acme", NAME, DUMMY, "민지")["id"], "민지")
    monkeypatch.setenv(NAME, "ENV-ONLY-DUMMY-VALUE")
    assert has_api_key("video")  # db 값이 있다
    monkeypatch.setenv("AX_SECRET_STORE", "env")
    assert resolver.get_secret(NAME) == "ENV-ONLY-DUMMY-VALUE"
    monkeypatch.delenv(NAME)
    assert not has_api_key("video")  # env 모드에서는 DB 값을 쓰지 않는다
