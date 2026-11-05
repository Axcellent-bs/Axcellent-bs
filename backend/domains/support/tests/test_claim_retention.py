"""클레임 기록 보유기간 삭제: 팀 기준(방침 제4조 ⑥ 5년), 기본 dry-run, 사람이 켜면 집행."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events as bus
from support_agent import claim_handlers, claim_retention, handlers
from support_agent.claim_schemas import Claim, ClaimAudit
from support_agent.claims import ClaimStore
from support_agent.main import app
from support_agent.store import InquiryStore

NAME = "홍길동"
NOTE = "고객이 010-1234-5678로 전화했다"


def _claim(store: ClaimStore, *, years: float, claim_id: int | None = None) -> Claim:
    """`years`년 전에 접수한 클레임 한 건을 저장소에 직접 넣는다."""
    stamp = (datetime.now(UTC) - timedelta(days=365.25 * years)).isoformat()
    claim = Claim(
        id=claim_id or (len(store.list()) + 1),
        context_id="ctx_1",
        order_id="ord_1",
        order_no="ORD-1",
        buyer_id="buy_0001",
        customer=NAME,
        type="cancel",
        reason="change_of_mind",
        status="completed",
        created_at=stamp,
        updated_at=stamp,
        outcome="approved",
        resolution="환불",
        requires_return=False,
        expected_refund=21000,
        refund_paid=21000,
        agent_note=NOTE,
        inspection_note=NOTE,
        audit=[
            ClaimAudit(
                actor="agent", action="approve", detail={"note": NOTE, "amount": 21000}, at=stamp
            )
        ],
    )
    store._claims[claim.id] = claim
    return claim


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> ClaimStore:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.delenv(claim_retention.ENV_DAYS, raising=False)
    monkeypatch.delenv(claim_retention.ENV_APPLY, raising=False)
    bus.reset_mock_bus()
    claims = ClaimStore()
    monkeypatch.setattr(claim_handlers, "CLAIMS", claims)
    monkeypatch.setattr(handlers, "STORE", InquiryStore())
    return claims


def call(skill: str, input: dict[str, Any]) -> dict[str, Any]:
    body = {
        "meta": {
            "context_id": "ctx_inq_0001",
            "token": "mock:dashboard-api",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            "idempotency_key": f"{skill}:{uuid.uuid4().hex}",
        },
        "skill": skill,
        "input": input,
    }
    return TestClient(app).post("/a2a", json=body).json()


# ---------------------------------------------------------------- 팀 기준 (보유 일수)
def test_default_is_five_years(store: ClaimStore) -> None:
    assert claim_retention.retention_days() == 1825


@pytest.mark.parametrize("raw", ["", "abc", "0", "-5", "99999"])
def test_a_bad_value_falls_back_to_five_years(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv(claim_retention.ENV_DAYS, raw)
    assert claim_retention.retention_days() == 1825


def test_the_days_can_be_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(claim_retention.ENV_DAYS, "730")
    assert claim_retention.retention_days() == 730


def test_only_claims_past_five_years_are_expired(store: ClaimStore) -> None:
    old = _claim(store, years=5.1)
    fresh = _claim(store, years=4.9)
    now = datetime.now(UTC)
    assert claim_retention.is_expired(old, now)
    assert not claim_retention.is_expired(fresh, now)


# ---------------------------------------------------------------- 치환
def test_redact_keeps_money_and_status_and_blanks_text() -> None:
    store = ClaimStore()
    claim = _claim(store, years=6)
    redacted, counts = claim_retention.redact(claim, "2026-11-05T00:00:00Z", 1825)
    assert redacted.customer == claim_retention.PURGED
    assert redacted.agent_note == redacted.inspection_note == claim_retention.PURGED
    assert redacted.expected_refund == 21000 and redacted.status == "completed"
    assert redacted.buyer_id == "buy_0001" and redacted.order_id == "ord_1"
    assert redacted.purged_at == "2026-11-05T00:00:00Z"
    assert NAME not in redacted.model_dump_json() and NOTE not in redacted.model_dump_json()
    first = redacted.audit[0]
    assert first.detail == {"note": claim_retention.PURGED, "amount": 21000}  # 숫자는 남긴다
    last = redacted.audit[-1]
    assert last.action == "retention_purge" and last.detail["retention_days"] == 1825
    assert counts == {"customer": 1, "notes": 2, "audit_details": 1}
    assert claim.customer == NAME  # 원본은 바꾸지 않는다


# ---------------------------------------------------------------- skill: 기본 dry-run
def test_the_skill_only_counts_until_a_human_turns_it_on(store: ClaimStore) -> None:
    old = _claim(store, years=6)
    result = call("purge_expired_claims", {})
    assert result["status"] == "completed" and result["outcome"] == "dry_run"
    report = result["output"]
    assert report["count"] == 1 and report["applied"] is False and report["apply_enabled"] is False
    assert store.get(old.id).customer == NAME and not store.get(old.id).purged_at


def test_the_skill_purges_once_a_human_turns_it_on(
    store: ClaimStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(claim_retention.ENV_APPLY, "1")
    old = _claim(store, years=6)
    fresh = _claim(store, years=1)
    result = call("purge_expired_claims", {})
    assert result["status"] == "completed" and result["output"]["applied"] is True
    assert result["output"]["count"] == 1 and result["output"]["items"][0]["claim_id"] == old.id
    assert store.get(old.id).customer == claim_retention.PURGED
    assert store.get(old.id).purged_at
    assert store.get(fresh.id).customer == NAME  # 5년이 안 된 클레임은 그대로다


def test_only_the_value_one_turns_the_apply_switch_on(
    store: ClaimStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _claim(store, years=6)
    for raw in ("true", "yes", "on", "0", ""):  # 잘못 적어도 지우지 않는 쪽이다
        monkeypatch.setenv(claim_retention.ENV_APPLY, raw)
        assert call("purge_expired_claims", {})["outcome"] == "dry_run"


def test_nothing_expired_does_nothing(store: ClaimStore) -> None:
    _claim(store, years=1)
    result = call("purge_expired_claims", {})
    assert result["outcome"] == "nothing_expired" and result["output"]["count"] == 0


def test_a_second_run_does_not_purge_again(
    store: ClaimStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(claim_retention.ENV_APPLY, "1")
    old = _claim(store, years=6)
    call("purge_expired_claims", {})
    again = call("purge_expired_claims", {})
    assert again["outcome"] == "nothing_expired"
    # 감사 내력은 한 번만 더해진다
    actions = [a.action for a in store.get(old.id).audit]
    assert actions.count("retention_purge") == 1


def test_a_shorter_setting_expires_younger_claims(
    store: ClaimStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(claim_retention.ENV_DAYS, "365")
    monkeypatch.setenv(claim_retention.ENV_APPLY, "1")
    claim = _claim(store, years=2)
    call("purge_expired_claims", {})
    assert store.get(claim.id).purged_at
