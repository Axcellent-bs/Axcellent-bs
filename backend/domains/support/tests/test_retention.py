"""시험 문의 원문 삭제(치환) CLI. 설계 4.3.

DB 없이 `MemoryRepo`(conftest)로 "집행 → 다시 시작해도 지워진 채"를 확인한다.
"""

import json
from datetime import UTC, datetime

import pytest

from support_agent import persistence, privacy_audit, retention
from support_agent.claim_schemas import Claim
from support_agent.claims import ClaimStore
from support_agent.persistence import PersistentInquiryStore
from support_agent.schemas import (
    Analysis,
    Classification,
    Draft,
    Feedback,
    ResolutionNote,
    Summary,
    Validation,
)
from support_agent.store import InquiryStore

SECRET = "010-1234-5678"
NAME = "홍길동"
NOW = datetime(2026, 10, 30, 10, 0, tzinfo=UTC)


def _analysis() -> Analysis:
    return Analysis(
        classification=Classification(
            intent_ko=f"{NAME} 환불 문의",
            category="payment",
            sentiment="calm",
            urgency="medium",
            search_query=f"{NAME} 환불",
            pii_masked=["phone"],
            keywords=["환불", NAME],
            message_ko=f"{NAME}입니다. 환불해 주세요",
            request_points_ko=[f"{NAME} 환불 요청"],
        ),
        drafts=[
            Draft(
                style="empathetic",
                reply=f"{NAME} 고객님 안녕하세요",
                validation=Validation(passed=True),
                reply_ko=f"{NAME} 고객님",
            )
        ],
    )


def _make(store: InquiryStore, subject: str = "[시험] 영수증 대조", status: str = "resolved"):
    conversation = store.create(
        context_id="ctx_1",
        tenant_id="catbooks",
        user_id=1,
        customer_name=NAME,
        subject=subject,
        order_id="ord_1",
        order_no="1",
        first_message=f"연락처 {SECRET} 입니다",
        analysis=_analysis(),
    )
    store.log_activity(conversation.id, kind="note", title="메모", detail=f"{NAME} {SECRET}")
    store.set_feedback(
        conversation.id,
        Feedback(
            rating=4, resolved=True, comment=f"{NAME} 감사", created_at="2026-10-30T00:00:00Z"
        ),
    )
    conversation.messages[0].display_text = f"{NAME} ***"
    conversation.messages[0].softened_text = f"{NAME} 정중한 글"
    conversation.subject_display = f"{NAME} 제목"
    store.add_resolution_note(
        conversation.id,
        ResolutionNote(
            id=1,
            author="상담원A",
            root_cause=f"{NAME} {SECRET}",
            action_taken=None,
            result="처리함",
            created_at="2026-10-30T00:00:00Z",
        ),
    )
    conversation.summary = Summary(
        summary_ko=NAME,
        issue_ko=NAME,
        actions_ko=[NAME],
        next_action_ko=NAME,
        category="payment",
        resolved=True,
        escalate=False,
        escalate_reason_ko=NAME,
        tags=[NAME],
    )
    return store.set_status(conversation.id, status)  # type: ignore[arg-type]


def test_dry_run_changes_nothing_and_reports_counts_only() -> None:
    store, claims = InquiryStore(), ClaimStore()
    conversation = _make(store)
    before = conversation.model_dump_json()
    plans = retention.run(store, claims, [conversation.id], apply=False, now=NOW)
    assert plans[0].action == "purge"
    assert store.get(conversation.id).model_dump_json() == before  # type: ignore[union-attr]
    assert store.privacy_audit == []
    line = retention._line(plans[0], apply=False)
    assert NAME not in line and SECRET not in line


def test_apply_replaces_text_and_keeps_statistics() -> None:
    store, claims = InquiryStore(), ClaimStore()
    conversation = _make(store)
    retention.run(store, claims, [conversation.id], apply=True, now=NOW)
    done = store.get(conversation.id)
    assert done is not None
    assert done.purged_at == "2026-10-30T10:00:00Z"
    assert (done.customer_name, done.subject, done.preview) == (retention.PURGED,) * 3
    assert done.messages[0].text == retention.PURGED
    classification = done.messages[0].analysis.classification  # type: ignore[union-attr]
    assert classification.keywords == [] and classification.pii_masked == ["phone"]
    assert classification.category == "payment"
    assert classification.message_ko == retention.PURGED and classification.request_points_ko == []
    draft = done.messages[0].analysis.drafts[0]  # type: ignore[union-attr,index]
    assert draft.reply == retention.PURGED and draft.reply_ko == retention.PURGED
    assert done.feedback and done.feedback.comment == "" and done.feedback.rating == 4
    assert done.activity[0].detail == "" and done.activity[0].data == {}
    assert done.activity[0].kind == "note"  # 건수·종류·시각은 남긴다
    assert (done.status, len(done.messages), done.order_id) == ("resolved", 1, "ord_1")
    dumped = done.model_dump_json()
    assert NAME not in dumped and SECRET not in dumped


def test_apply_is_idempotent_and_writes_one_audit_record_without_content() -> None:
    store, claims = InquiryStore(), ClaimStore()
    conversation = _make(store)
    retention.run(store, claims, [conversation.id], apply=True, now=NOW)
    second = retention.run(store, claims, [conversation.id], apply=True, now=NOW)
    assert second[0].action == "already_purged"
    records = [r for r in store.privacy_audit if r["record_type"] == "retention_purge"]
    assert len(records) == 1
    record = records[0]
    assert record["criterion"] == "explicit_test_inquiry"
    assert record["executor_role"] == "operator"
    assert record["purged"] == {"messages": 1, "activity": 1, "feedback": 1, "resolution_notes": 1}
    assert record["kept"] == {"messages": 1, "activity": 1, "claims": 0}
    assert NAME not in json.dumps(record, ensure_ascii=False) and SECRET not in json.dumps(record)


def test_audit_skill_view_shows_the_purge_record() -> None:
    store, claims = InquiryStore(), ClaimStore()
    conversation = _make(store)
    retention.run(store, claims, [conversation.id], apply=True, now=NOW)
    audit = privacy_audit.build_audit(store, conversation.id)
    assert len(audit.retention_purge) == 1
    only = privacy_audit.build_audit(store, conversation.id, ["masking"])
    assert only.retention_purge == []


@pytest.mark.parametrize(
    ("status", "reason"), [("open", "status_open"), ("escalated", "status_escalated")]
)
def test_unfinished_inquiries_are_blocked(status: str, reason: str) -> None:
    store, claims = InquiryStore(), ClaimStore()
    conversation = _make(store, status=status)
    plan = retention.run(store, claims, [conversation.id], apply=True, now=NOW)[0]
    assert plan.action == "blocked" and plan.reasons == [reason]
    assert store.get(conversation.id).purged_at is None  # type: ignore[union-attr]
    assert store.privacy_audit == []


def test_non_test_inquiries_are_blocked() -> None:
    store, claims = InquiryStore(), ClaimStore()
    conversation = _make(store, subject="결제가 안 돼요")
    plan = retention.run(store, claims, [conversation.id], apply=True, now=NOW)[0]
    assert plan.action == "blocked" and plan.reasons == ["not_test_data"]
    assert store.get(conversation.id).messages[0].text != retention.PURGED  # type: ignore[union-attr]


def test_inquiry_with_a_claim_activity_ref_is_blocked() -> None:
    store, claims = InquiryStore(), ClaimStore()
    conversation = _make(store)
    store.log_activity(conversation.id, kind="claim_created", title="클레임", ref="claim:3")
    plan = retention.run(store, claims, [conversation.id], apply=True, now=NOW)[0]
    assert plan.action == "blocked" and plan.reasons == ["claim_linked"]


def test_missing_inquiry_is_reported_not_created() -> None:
    plan = retention.run(InquiryStore(), ClaimStore(), [99], apply=True, now=NOW)[0]
    assert plan.action == "not_found"


def test_purge_survives_a_restart_with_the_persistent_store(memory_repo) -> None:  # type: ignore[no-untyped-def]
    repo = memory_repo()
    store = PersistentInquiryStore(repo)
    conversation = _make(store)
    retention.run(store, ClaimStore(), [conversation.id], apply=True, now=NOW)
    restarted = PersistentInquiryStore(repo)
    done = restarted.get(conversation.id)
    assert done is not None and done.purged_at and done.messages[0].text == retention.PURGED
    assert any(r["record_type"] == "retention_purge" for r in restarted.privacy_audit)
    stored = " ".join(doc for rows in repo.tables.values() for doc in rows.values())
    assert NAME not in stored and SECRET not in stored


def test_cli_needs_a_database_and_never_touches_memory(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(persistence.DSN_ENV, raising=False)
    assert retention.main(["--inquiry-id", "1", "--apply"]) == 2
    assert persistence.DSN_ENV in capsys.readouterr().err


def test_cli_defaults_to_dry_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], memory_repo
) -> None:  # type: ignore[no-untyped-def]
    repo = memory_repo()
    store = PersistentInquiryStore(repo)
    conversation = _make(store)
    monkeypatch.setenv(persistence.DSN_ENV, "postgresql://unused")
    monkeypatch.setattr(persistence, "get_repo", lambda: repo)
    assert retention.main(["--inquiry-id", str(conversation.id)]) == 0
    out = capsys.readouterr().out
    assert "지울 예정" in out and "dry-run" in out
    assert PersistentInquiryStore(repo).get(conversation.id).purged_at is None  # type: ignore[union-attr]
    assert retention.main(["--inquiry-id", str(conversation.id), "--apply"]) == 0
    assert PersistentInquiryStore(repo).get(conversation.id).purged_at  # type: ignore[union-attr]


def test_abuse_display_fields_and_resolution_notes_are_blanked() -> None:
    store, claims = InquiryStore(), ClaimStore()
    conversation = _make(store)
    retention.run(store, claims, [conversation.id], apply=True, now=NOW)
    done = store.get(conversation.id)
    assert done is not None
    message = done.messages[0]
    assert message.display_text == retention.PURGED and message.softened_text == retention.PURGED
    assert done.subject_display == retention.PURGED
    note = done.resolution_notes[0]
    assert (
        note.author == "상담원A" and note.created_at == "2026-10-30T00:00:00Z"
    )  # 작성자·시각은 남긴다
    assert note.root_cause == retention.PURGED and note.result == retention.PURGED
    assert note.action_taken is None  # 원래 비어 있던 칸은 그대로


def test_inquiry_linked_to_a_claim_by_conversation_id_is_blocked() -> None:
    store, claims = InquiryStore(), ClaimStore()
    conversation = _make(store)
    claims._claims[7] = Claim.model_construct(id=7, conversation_id=conversation.id, order_id=None)
    plan = retention.run(store, claims, [conversation.id], apply=True, now=NOW)[0]
    assert plan.action == "blocked" and plan.reasons == ["claim_linked"]


def test_missing_audit_record_after_a_purge_is_backfilled_once() -> None:
    store, claims = InquiryStore(), ClaimStore()
    conversation = _make(store)
    retention.run(store, claims, [conversation.id], apply=True, now=NOW)
    store.privacy_audit.clear()  # 지운 뒤 감사 기록을 쓰기 전에 중단된 상황
    dry = retention.run(store, claims, [conversation.id], apply=False, now=NOW)[0]
    assert dry.action == "already_purged" and dry.note == "audit_backfill"
    assert store.privacy_audit == []  # dry-run은 쓰지 않는다
    retention.run(store, claims, [conversation.id], apply=True, now=NOW)
    retention.run(store, claims, [conversation.id], apply=True, now=NOW)
    records = [r for r in store.privacy_audit if r["record_type"] == "retention_purge"]
    assert len(records) == 1 and records[0]["criterion"] == retention.BACKFILL_CRITERION
    assert records[0]["purged"] == {}


def test_a_stale_service_cache_cannot_bring_the_original_back(memory_repo) -> None:  # type: ignore[no-untyped-def]
    repo = memory_repo()
    service = PersistentInquiryStore(repo)  # 실행 중인 서비스: 메모리에 원문을 들고 있다
    conversation = _make(service)
    cli = PersistentInquiryStore(repo)  # 별도 프로세스인 CLI
    retention.run(cli, ClaimStore(), [conversation.id], apply=True, now=NOW)
    assert service.get(conversation.id).messages[0].text != retention.PURGED  # type: ignore[union-attr]

    # 서비스가 재시작 전에 그 문의에 쓴다: 상태 변경, 활동 기록, 고객 메시지, 피드백
    service.set_status(conversation.id, "resolved")
    service.log_activity(conversation.id, kind="note", title="메모", detail=f"{NAME} {SECRET}")
    service.set_feedback(
        conversation.id,
        Feedback(rating=1, resolved=False, comment=NAME, created_at="2026-10-30T00:00:00Z"),
    )
    stored = " ".join(doc for doc in repo.tables["inquiries"].values())
    assert NAME not in stored and SECRET not in stored
    done = PersistentInquiryStore(repo).get(conversation.id)
    assert done is not None and done.purged_at and done.messages[0].text == retention.PURGED
    assert service.get(conversation.id).purged_at  # type: ignore[union-attr]  # 서비스도 지운 모습을 받아들인다
    assert NAME not in service.get(conversation.id).model_dump_json()  # type: ignore[union-attr]
