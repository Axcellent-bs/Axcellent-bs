"""상태 파일(`SAFETY_STATE_DB`): 재시작해도 실행 기록·멱등성·보류 큐·감사 로그가 남는다.

재시작은 같은 파일로 새 `Store`·`Gate`를 만드는 것으로 흉내 낸다.
파일에 못 쓰면 실행하지 않는다(fail-closed).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import HoldReason
from ax_safety.gate import Gate
from ax_safety.main import create_app
from ax_safety.rules import load_registry, load_thresholds
from ax_safety.store import Store, StoreWriteError


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "safety_state.db"


@pytest.fixture
def make_gate(clock: Any, verifier: Any, sent: Any):
    stores: list[Store] = []

    def make(path: Path | None) -> Gate:
        store = Store(path)
        stores.append(store)
        return Gate(load_registry(), load_thresholds(), store, verifier, sent, now=clock)

    yield make
    for store in stores:
        store.close()


def _settlement(act, key: str, who: str = "support-ai"):
    return act("confirm_settlement", "support", key, period="2026-W44", requested_by=who)


@pytest.mark.asyncio
async def test_restart_keeps_executions_reviews_and_idempotency(make_gate, db, coupon, act) -> None:
    gate = make_gate(db)
    executed = await gate.execute(coupon(2, 1000, key="small"))
    held = await gate.execute(_settlement(act, "w44"))
    assert (executed.status, held.status) == ("executed", "held")
    audit_before = [e.model_dump() for e in gate.store.list_audit()]

    again = make_gate(db)  # 재시작
    assert [r.execution_id for r in again.store.list_executions()] == [
        executed.execution_id,
        held.execution_id,
    ]
    pending = again.store.list_reviews("pending")
    assert [r.review_id for r in pending] == [held.review_id]
    original = gate.store.get_review(held.review_id or "")
    assert original is not None
    assert pending[0].expires_at == original.expires_at  # 마감은 그대로
    assert [e.model_dump() for e in again.store.list_audit()] == audit_before

    # 같은 멱등성 키는 재시작 뒤에도 같은 판정을 돌려준다 (새 보류 건을 만들지 않는다)
    assert await again.execute(coupon(2, 1000, key="small")) == executed
    assert await again.execute(_settlement(act, "w44")) == held
    assert len(again.store.list_reviews()) == 1
    # 감사 로그 번호는 이어진다
    ids = [e.log_id for e in again.store.list_audit()]
    assert ids == list(range(1, len(ids) + 1))


@pytest.mark.asyncio
async def test_pending_review_can_be_approved_after_restart(make_gate, db, act) -> None:
    gate = make_gate(db)
    held = await gate.execute(_settlement(act, "w44"))

    again = make_gate(db)
    review = await again.decide_review(held.review_id or "", "approved", "byunghwan", "확인")
    assert (review.status, review.decided_by) == ("approved", "byunghwan")
    replay = await again.execute(_settlement(act, "w44"))
    assert (replay.status, replay.execution_id) == ("executed", held.execution_id)

    third = make_gate(db)  # 결정도 남는다
    kept = third.store.get_review(held.review_id or "")
    assert kept is not None
    assert (kept.status, kept.decided_by, kept.note) == ("approved", "byunghwan", "확인")
    assert (await third.execute(_settlement(act, "w44"))).status == "executed"
    assert third.store.list_audit()[-1].step == "idempotent_replay"
    assert any(e.step == "review_approved" for e in third.store.list_audit())


@pytest.mark.asyncio
async def test_expiry_still_applies_after_restart(make_gate, db, clock, coupon) -> None:
    gate = make_gate(db)
    held = await gate.execute(coupon(20, 20_000, purpose_code="campaign", key="slow"))
    clock.advance(599)

    again = make_gate(db)
    assert await again.expire_reviews() == []
    clock.advance(1)
    assert [r.review_id for r in await again.expire_reviews()] == [held.review_id]
    replay = await again.execute(coupon(20, 20_000, purpose_code="campaign", key="slow"))
    assert (replay.status, replay.hold_reason) == ("rejected", HoldReason.REVIEW_TIMEOUT)

    third = make_gate(db)
    assert third.store.get_review(held.review_id or "").status == "expired"  # type: ignore[union-attr]


def _break(store: Store) -> None:
    """상태 파일 연결을 닫아 다음 쓰기가 실패하게 한다."""
    store.close()


@pytest.mark.asyncio
async def test_write_failure_is_not_executed(make_gate, db, coupon) -> None:
    gate = make_gate(db)
    _break(gate.store)
    decision = await gate.execute(coupon(2, 1000, key="small"))
    assert (decision.status, decision.grade) == ("rejected", "C")
    assert gate.store.by_key("default", "small") is None  # 기록이 없으니 다시 요청하면 새로 판정
    assert gate.store.list_executions() == []


@pytest.mark.asyncio
async def test_write_failure_after_audit_is_not_executed(
    make_gate, db, coupon, monkeypatch
) -> None:
    """감사 로그는 남았지만 실행 기록을 못 쓰면 실행하지 않는다."""
    gate = make_gate(db)

    def fail(*_: Any, **__: Any) -> None:
        raise StoreWriteError("디스크가 가득 참")

    monkeypatch.setattr(gate.store, "add_execution", fail)
    decision = await gate.execute(coupon(2, 1000, key="small"))
    assert (decision.status, decision.grade) == ("rejected", "C")


@pytest.mark.asyncio
async def test_approval_that_cannot_be_written_does_not_execute(make_gate, db, act) -> None:
    gate = make_gate(db)
    held = await gate.execute(_settlement(act, "w44"))
    _break(gate.store)
    with pytest.raises(StoreWriteError):
        await gate.decide_review(held.review_id or "", "approved", "byunghwan", None)
    record = gate.store.get_execution(held.execution_id)
    assert record is not None and record.decision.status == "held"
    assert gate.store.get_review(held.review_id or "").status == "pending"  # type: ignore[union-attr]

    again = make_gate(db)  # 파일에도 승인이 남지 않았다
    assert again.store.get_review(held.review_id or "").status == "pending"  # type: ignore[union-attr]
    assert (await again.execute(_settlement(act, "w44"))).status == "held"


def test_decision_api_answers_503_when_state_cannot_be_written(make_gate, db, act) -> None:
    import asyncio

    gate = make_gate(db)
    held = asyncio.run(gate.execute(_settlement(act, "w44")))
    _break(gate.store)
    client = TestClient(create_app(gate, expire_interval=None))
    response = client.post(
        f"/api/safety/reviews/{held.review_id}/decision",
        json={"decision": "approved"},
        headers={"Authorization": "Bearer human:byunghwan"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "STATE_UNAVAILABLE"


def test_env_turns_the_state_file_on(monkeypatch: pytest.MonkeyPatch, db: Path) -> None:
    monkeypatch.setenv("SAFETY_STATE_DB", str(db))
    store = Store()
    try:
        assert store.persistent and db.exists()
        with sqlite3.connect(db) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        assert {"executions", "idempotency_keys", "reviews", "audit_logs"} <= tables
    finally:
        store.close()


def test_without_env_the_store_stays_in_memory() -> None:
    assert not Store().persistent
