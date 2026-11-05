"""개인정보 삭제: 고객 요청 삭제(사람 승인)와 3년 보유기간 삭제.

- 고객 요청: 처리 중인 문의·클레임이 이어진 문의는 보존 의무로 남기고 `retained_until`을 알린다.
- 3년: 받은 지 3년이 지난 문의는 상태·클레임에 상관없이 지운다.
- 행은 남기고 원문만 `[삭제됨]`으로 바꾼다. 감사 기록에는 문의 내용이 없다.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events as bus
from ax_contracts.schemas import AgentRequest, AgentResult, ExecuteDecision, TaskStatus
from support_agent import claim_handlers, handlers, retention
from support_agent.claims import ClaimStore
from support_agent.main import app
from support_agent.schemas import Analysis
from support_agent.store import InquiryStore

CTX = "ctx_inq_0001"
SECRET = "홍길동"


def _make(store: InquiryStore, *, user_id: int | None = 1, status: str = "resolved") -> int:
    conversation = store.create(
        context_id=CTX,
        tenant_id="catbooks",
        user_id=user_id,
        customer_name=SECRET,
        subject=f"{SECRET} 환불 문의",
        order_id=5,
        order_no="ORD-5",
        first_message=f"{SECRET}입니다. 010-1234-5678",
        analysis=Analysis(),
    )
    if status != "open":
        store.set_status(conversation.id, status)
    return conversation.id


def _age(store: InquiryStore, inquiry_id: int, years: float) -> None:
    """문의를 받은 시각을 `years`년 전으로 돌린다."""
    conversation = store.get(inquiry_id)
    stamp = (datetime.now(UTC) - timedelta(days=365.25 * years)).isoformat()
    for message in conversation.messages:
        message.created_at = stamp


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> InquiryStore:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.delenv(retention.ENV_INQUIRY_APPLY, raising=False)
    bus.reset_mock_bus()
    store = InquiryStore()
    monkeypatch.setattr(handlers, "STORE", store)
    monkeypatch.setattr(claim_handlers, "CLAIMS", ClaimStore())

    async def no_remote_claims(target: str, req: AgentRequest) -> AgentResult:
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output={"items": []})

    monkeypatch.setattr(handlers, "SEND", no_remote_claims)
    return store


def call(skill: str, input: dict[str, Any], key: str | None = None) -> dict[str, Any]:
    body = {
        "meta": {
            "context_id": CTX,
            "token": "mock:dashboard-api",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            "idempotency_key": key or f"{skill}:{uuid.uuid4().hex}",
        },
        "skill": skill,
        "input": input,
    }
    return TestClient(app).post("/a2a", json=body).json()


# ---------------------------------------------------------------- 3년 보유기간
def test_the_deadline_is_three_years_after_receiving() -> None:
    store = InquiryStore()
    inquiry_id = _make(store)
    conversation = store.get(inquiry_id)
    conversation.messages[0].created_at = "2023-03-10T09:00:00+00:00"
    assert retention.retention_deadline(conversation) == datetime(2026, 3, 10, 9, tzinfo=UTC)
    assert retention.is_expired(conversation, datetime(2026, 3, 10, 9, tzinfo=UTC))
    assert not retention.is_expired(conversation, datetime(2026, 3, 10, 8, 59, tzinfo=UTC))


def test_a_leap_day_deadline_moves_to_march_first() -> None:
    store = InquiryStore()
    conversation = store.get(_make(store))
    conversation.messages[0].created_at = "2024-02-29T00:00:00+00:00"
    assert retention.retention_deadline(conversation) == datetime(2027, 3, 1, tzinfo=UTC)


def test_expired_inquiries_are_purged_whatever_their_state(
    env: InquiryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(retention.ENV_INQUIRY_APPLY, "1")
    old_resolved = _make(env)
    old_open = _make(env, status="open")
    fresh = _make(env)
    for inquiry_id in (old_resolved, old_open):
        _age(env, inquiry_id, 3.1)
    _age(env, fresh, 2.9)

    result = call("purge_expired_inquiries", {})
    assert result["status"] == "completed", result
    report = result["output"]
    assert report["applied"] is True and report["purge_count"] == 2
    assert {i["inquiry_id"] for i in report["items"]} == {old_resolved, old_open}

    for inquiry_id in (old_resolved, old_open):
        conversation = env.get(inquiry_id)
        assert conversation.purged_at and conversation.customer_name == retention.PURGED
        assert SECRET not in conversation.model_dump_json()
    assert not env.get(fresh).purged_at  # 3년이 안 된 문의는 그대로다
    assert SECRET in env.get(fresh).subject


def test_expiry_writes_an_audit_record_without_content(
    env: InquiryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(retention.ENV_INQUIRY_APPLY, "1")
    inquiry_id = _make(env)
    _age(env, inquiry_id, 4)
    call("purge_expired_inquiries", {})
    records = [r for r in env.privacy_audit if r.get("record_type") == "retention_purge"]
    assert len(records) == 1
    assert records[0]["criterion"] == "retention_3y" and records[0]["executor_role"] == "system"
    assert SECRET not in str(records)


def test_nothing_expired_does_nothing(env: InquiryStore) -> None:
    _make(env)
    result = call("purge_expired_inquiries", {})
    assert result["outcome"] == "nothing_expired" and result["output"]["applied"] is False


def test_the_same_day_call_does_not_purge_or_record_twice(
    env: InquiryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(retention.ENV_INQUIRY_APPLY, "1")
    inquiry_id = _make(env)
    _age(env, inquiry_id, 5)
    first = call("purge_expired_inquiries", {}, key="retention-expire:today")
    again = call("purge_expired_inquiries", {}, key="retention-expire:today")
    assert first["output"]["purge_count"] == 1
    assert again["outcome"] == "nothing_expired"  # 이미 지웠다
    assert len([r for r in env.privacy_audit if r.get("record_type") == "retention_purge"]) == 1


def test_purged_inquiries_leave_the_list(
    env: InquiryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(retention.ENV_INQUIRY_APPLY, "1")
    gone = _make(env)
    kept = _make(env)
    _age(env, gone, 4)
    call("purge_expired_inquiries", {})
    ids = [i["id"] for i in call("list_inquiries", {})["output"]["items"]]
    assert gone not in ids and kept in ids


# ---------------------- 팀 방식: 기본 dry-run, 사람이 켜면 집행
def test_expiry_only_counts_until_a_human_turns_it_on(env: InquiryStore) -> None:
    old = _make(env)
    _age(env, old, 4)
    result = call("purge_expired_inquiries", {})
    assert result["outcome"] == "dry_run" and result["output"]["applied"] is False
    assert result["output"]["apply_enabled"] is False and result["output"]["purge_count"] == 1
    assert not env.get(old).purged_at and SECRET in env.get(old).subject


def test_only_the_value_one_turns_the_inquiry_switch_on(
    env: InquiryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _make(env)
    _age(env, old, 4)
    for raw in ("true", "yes", "on", "0", ""):  # 잘못 적어도 지우지 않는 쪽이다
        monkeypatch.setenv(retention.ENV_INQUIRY_APPLY, raw)
        assert call("purge_expired_inquiries", {})["outcome"] == "dry_run"
    assert not env.get(old).purged_at


def test_the_preview_shows_whether_the_switch_is_on(
    env: InquiryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = call("preview_data_purge", {"mode": "expired"})
    assert result["output"]["apply_enabled"] is False
    monkeypatch.setenv(retention.ENV_INQUIRY_APPLY, "1")
    assert call("preview_data_purge", {"mode": "expired"})["output"]["apply_enabled"] is True


# ---------------------- 클레임이 이어진 문의는 클레임 기준(5년)
def test_a_claim_linked_inquiry_is_kept_past_three_years_until_the_claim_deadline(
    env: InquiryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(retention.ENV_INQUIRY_APPLY, "1")
    linked = _make(env)
    plain = _make(env)
    for inquiry_id in (linked, plain):
        _age(env, inquiry_id, 4)  # 3년은 지났고 5년은 안 지났다
    env.log_activity(linked, kind="claim_created", title="클레임", ref="claim:7")
    result = call("purge_expired_inquiries", {})
    items = {i["inquiry_id"]: i for i in result["output"]["items"]}
    assert items[plain]["action"] == "purge"
    assert items[linked]["action"] == "blocked" and items[linked]["reasons"] == ["claim_linked"]
    assert items[linked]["retained_until"]  # 받은 날 + 5년
    assert env.get(plain).purged_at and not env.get(linked).purged_at
    assert result["output"]["retained_count"] == 1


def test_a_claim_linked_inquiry_is_purged_once_the_claim_deadline_passes(
    env: InquiryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(retention.ENV_INQUIRY_APPLY, "1")
    linked = _make(env)
    _age(env, linked, 5.2)
    env.log_activity(linked, kind="claim_created", title="클레임", ref="claim:7")
    result = call("purge_expired_inquiries", {})
    assert result["output"]["items"][0]["action"] == "purge"
    assert env.get(linked).purged_at


def test_claims_in_another_process_also_keep_the_inquiry(
    env: InquiryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(retention.ENV_INQUIRY_APPLY, "1")
    linked = _make(env)
    _age(env, linked, 4)

    async def claims_service(target: str, req: AgentRequest) -> AgentResult:
        items = [{"id": 1, "conversation_id": linked}]
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output={"items": items})

    monkeypatch.setattr(handlers, "SEND", claims_service)
    result = call("purge_expired_inquiries", {})
    assert result["output"]["items"][0]["reasons"] == ["claim_linked"]
    assert not env.get(linked).purged_at


def test_unreadable_claims_stop_the_expiry_sweep(
    env: InquiryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(retention.ENV_INQUIRY_APPLY, "1")
    old = _make(env)
    _age(env, old, 4)

    async def down(target: str, req: AgentRequest) -> AgentResult:
        raise ConnectionError("claims service down")

    monkeypatch.setattr(handlers, "SEND", down)
    result = call("purge_expired_inquiries", {})
    assert result["status"] == "failed" and "claims_unavailable" in str(result)
    assert not env.get(old).purged_at  # 보존 의무를 모르면 지우지 않는다


def test_customer_request_retained_until_follows_the_claim_deadline(env: InquiryStore) -> None:
    request = _make(env)
    claimed = _make(env)
    _age(env, claimed, 1)
    env.log_activity(claimed, kind="claim_created", title="클레임", ref="claim:7")
    result = call("preview_data_purge", {"mode": "customer_request", "inquiry_id": request})
    items = {i["inquiry_id"]: i for i in result["output"]["items"]}
    until = datetime.fromisoformat(items[claimed]["retained_until"])
    received = retention.received_at(env.get(claimed))
    assert (until - received.replace(tzinfo=None)).days in (1824, 1825, 1826)  # 클레임 5년


# ---------------------------------------------------------------- 고객 요청 삭제
def test_customer_request_preview_separates_purgeable_and_retained(env: InquiryStore) -> None:
    request = _make(env)  # 삭제를 요청한 문의 (해결됨)
    other_done = _make(env)
    still_open = _make(env, status="open")
    with_claim = _make(env)
    other_customer = _make(env, user_id=2)
    env.log_activity(with_claim, kind="claim_created", title="클레임", ref="claim:7")

    result = call("preview_data_purge", {"mode": "customer_request", "inquiry_id": request})
    assert result["status"] == "completed", result
    items = {i["inquiry_id"]: i for i in result["output"]["items"]}
    assert items[request]["action"] == "purge" and items[other_done]["action"] == "purge"
    assert items[still_open]["reasons"] == ["status_open"]
    assert items[with_claim]["reasons"] == ["claim_linked"]
    assert items[with_claim]["retained_until"]  # 받은 날로부터 3년이 되는 날
    assert other_customer not in items  # 다른 구매자의 문의는 건드리지 않는다
    assert result["output"]["applied"] is False
    assert not env.get(request).purged_at  # 미리보기는 아무것도 바꾸지 않는다


def test_customer_request_is_held_for_a_human_and_purges_nothing(
    env: InquiryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _make(env)
    other_customer = _make(env, user_id=2)
    seen: list[str] = []

    async def held(action: str, *args: Any, **kwargs: Any) -> ExecuteDecision:
        seen.append(action)
        return ExecuteDecision(
            execution_id="exe_1",
            grade="C",
            status="held",
            hold_reason="unregistered_action",
            review_id="r1",
        )

    monkeypatch.setattr(handlers, "execute", held)
    result = call(
        "purge_customer_data", {"inquiry_id": request}, key=f"customer-deletion:{request}"
    )
    # 되돌릴 수 없는 삭제는 늘 사람이 정한다: 승인 액션(C)으로 부르고, 보류면 아무것도 지우지 않는다
    assert seen == ["approve_data_deletion"]
    assert result["outcome"] == "not_executed_held"
    assert not env.get(request).purged_at and not env.get(other_customer).purged_at


def test_customer_request_purges_only_that_buyer_once_approved(env: InquiryStore) -> None:
    """모의 관문은 바로 실행으로 돌려준다(승인된 뒤 같은 키로 다시 부른 것과 같다)."""
    request = _make(env)
    other_done = _make(env)
    still_open = _make(env, status="open")
    other_customer = _make(env, user_id=2)
    result = call("purge_customer_data", {"inquiry_id": request})
    assert result["status"] == "completed" and result["output"]["applied"] is True
    assert env.get(request).purged_at and env.get(other_done).purged_at
    assert not env.get(still_open).purged_at  # 처리 중인 문의는 남긴다
    assert not env.get(other_customer).purged_at  # 다른 구매자는 건드리지 않는다
    assert SECRET not in env.get(request).model_dump_json()


def test_customer_request_with_nothing_to_purge_asks_nobody(env: InquiryStore) -> None:
    still_open = _make(env, status="open")
    result = call("purge_customer_data", {"inquiry_id": still_open})
    assert result["outcome"] == "nothing_to_purge"
    assert result["output"]["items"][0]["reasons"] == ["status_open"]


def test_customer_request_without_a_buyer_cannot_be_matched(env: InquiryStore) -> None:
    anonymous = _make(env, user_id=None)
    result = call("preview_data_purge", {"mode": "customer_request", "inquiry_id": anonymous})
    assert result["output"]["items"][0]["reasons"] == ["no_buyer"]


def test_unknown_request_inquiry_is_not_found(env: InquiryStore) -> None:
    result = call("preview_data_purge", {"mode": "customer_request", "inquiry_id": 999})
    assert result["outcome"] == "not_found"


def test_unreadable_claims_block_the_purge(
    env: InquiryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _make(env)

    async def down(target: str, req: AgentRequest) -> AgentResult:
        raise ConnectionError("claims service down")

    monkeypatch.setattr(handlers, "SEND", down)
    result = call("purge_customer_data", {"inquiry_id": request})
    assert result["status"] == "failed" and "claims_unavailable" in str(result)
    assert not env.get(request).purged_at  # 보존 의무를 모르면 지우지 않는다


def test_claims_in_another_process_are_retained(
    env: InquiryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _make(env)
    claimed = _make(env)

    async def claims_service(target: str, req: AgentRequest) -> AgentResult:
        items = [{"id": 1, "conversation_id": claimed}]
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output={"items": items})

    monkeypatch.setattr(handlers, "SEND", claims_service)
    result = call("preview_data_purge", {"mode": "customer_request", "inquiry_id": request})
    items = {i["inquiry_id"]: i for i in result["output"]["items"]}
    assert items[claimed]["reasons"] == ["claim_linked"]
    assert items[request]["action"] == "purge"


def test_customer_request_plan_applies_for_purgeable_ones_only(env: InquiryStore) -> None:
    """사람 승인 뒤 집행되는 계획(`plan_customer_request`)을 직접 확인한다."""
    request = _make(env)
    still_open = _make(env, status="open")
    plans = retention.plan_customer_request(
        env, claim_handlers.CLAIMS, request, set(), True, "2026-11-05T00:00:00Z"
    )
    by_id = {p.inquiry_id: p for p in plans}
    assert by_id[request].action == "purge" and by_id[still_open].action == "blocked"
    assert env.get(request).purged_at and not env.get(still_open).purged_at
    records = [r for r in env.privacy_audit if r.get("record_type") == "retention_purge"]
    assert [r["criterion"] for r in records] == ["customer_request"]
    assert SECRET not in str(records)
