"""시험·시연 문의의 원문 삭제(치환).

설계: `docs/notes/support/2026-10-28-privacy-audit-evidence-design.md` 4.3.

- 행은 지우지 않는다. 원문 필드를 `[삭제됨]`으로 바꾸고 `purged_at`을 남긴다. 개인정보 감사 기록이
  문의 ID로 묶인 추가 전용 기록이라 행을 지우면 고아가 된다.
- 지금은 운영자가 `--inquiry-id`로 지정하고, 제목이 `[시험]`·`[test]`로 시작하는 문의만 지운다.
  기한이 지난 문의를 자동으로 고르는 보유기간 삭제와 고객 요청 삭제는 이 도구의 범위가 아니다.
- 막는 것: 열린(`open`)·이관(`escalated`) 문의, 클레임이 이어진 문의(법정 보존 대상일 수 있다),
  시험 표시가 없는 문의. 이미 지운 문의는 아무것도 하지 않는다(멱등).
- 남기는 것: 분류·감정·긴급도·상태·시각·메시지 수·피드백 점수·이어진 클레임 ID·감사 기록.
  메시지 본문·활동 상세·피드백 글·해결 메모 본문은 비우고 건수·시각·작성자만 남긴다.
- 기본은 dry-run(건수만 센다). `--apply`로 집행한다. 출력과 감사 기록에는 문의 내용이 없다.
- 외부에 열린 삭제 경로는 만들지 않는다. 운영자가 CLI로만 실행한다
  (`python -m support_agent.retention --inquiry-id 5 [--apply]`).
- 제목 접두어는 고객도 쓸 수 있어서 안전장치가 아니라 실수 방지용이다. 실행 전에 운영자가
  지울 문의 ID를 직접 확인한다(dry-run으로 건수를 보고 `--apply`).
- `SUPPORT_PG_DSN`이 있어야 한다. 이 CLI는 별도 프로세스라 DSN이 없으면 서비스가 들고 있는 메모리를
  바꿀 수 없다. 집행한 뒤에는 support 서비스를 다시 시작해야 서비스의 캐시에도 반영된다.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from support_agent import persistence, privacy_audit
from support_agent.claims import ClaimStore
from support_agent.schemas import Conversation
from support_agent.store import InquiryStore

PURGED = "[삭제됨]"
TEST_MARKERS = ("[시험]", "[test]")
CRITERION = "explicit_test_inquiry"
BACKFILL_CRITERION = "audit_backfill"  # 지운 뒤 기록이 빠진 문의의 기록만 보충한다
EXECUTOR_ROLE = "operator"  # 사람 이름이 아니라 역할 코드. 감사 기록에도 이것만 남긴다


@dataclass
class Plan:
    inquiry_id: int
    action: str  # purge | already_purged | blocked | not_found
    reasons: list[str] = field(default_factory=list)
    purged: dict[str, int] = field(default_factory=dict)
    kept: dict[str, int] = field(default_factory=dict)
    note: str = ""
    retained_until: str | None = None  # 보존 의무로 남겨 둔 문의를 지우는 날 (보유기간 만료일)


# ---------------------------------------------------------------- 보유기간·고객 요청 삭제
RETENTION_YEARS = 3  # 개인정보를 이 기간을 넘겨 들고 있지 않는다 (문의를 받은 날부터)
CRITERION_EXPIRED = "retention_3y"
CRITERION_CUSTOMER = "customer_request"
SYSTEM_ROLE = "system"  # 보유기간이 지나 자동으로 지운 기록의 실행자 역할


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def received_at(conversation: Conversation) -> datetime | None:
    """문의를 받은 시각: 가장 이른 메시지 시각. 메시지가 없으면 마지막 변경 시각."""
    times = [t for m in conversation.messages if (t := _parse_time(m.created_at)) is not None]
    return min(times) if times else _parse_time(conversation.updated_at)


def retention_deadline(conversation: Conversation) -> datetime | None:
    """이 문의를 지워야 하는 날: 받은 날로부터 3년. 받은 시각을 못 읽으면 None."""
    created = received_at(conversation)
    if created is None:
        return None
    try:
        return created.replace(year=created.year + RETENTION_YEARS)
    except ValueError:  # 2월 29일 → 3년 뒤에 그날이 없으면 3월 1일
        return created.replace(year=created.year + RETENTION_YEARS, month=3, day=1)


def is_expired(conversation: Conversation, now: datetime) -> bool:
    deadline = retention_deadline(conversation)
    return deadline is not None and now >= deadline


def _is_test_data(conversation: Conversation) -> bool:
    return conversation.subject.strip().lower().startswith(tuple(m.lower() for m in TEST_MARKERS))


def _linked_claims(conversation: Conversation, claims: ClaimStore) -> list[int]:
    """이 문의에 이어진 클레임 ID. 활동 기록의 `claim:<id>`, 클레임의 문의 번호, 주문을 본다."""
    ids = {
        int(entry.ref.removeprefix("claim:"))
        for entry in conversation.activity
        if entry.ref and entry.ref.startswith("claim:") and entry.ref[6:].isdigit()
    }
    ids.update(int(c["id"]) for c in conversation.claims if str(c.get("id", "")).isdigit())
    ids.update(c.id for c in claims.list() if c.conversation_id == conversation.id)
    if conversation.order_id is not None:
        ids.update(c.id for c in claims.list() if c.order_id == conversation.order_id)
    return sorted(ids)


def blockers(conversation: Conversation, claims: ClaimStore) -> list[str]:
    reasons = []
    if conversation.status == "open":
        reasons.append("status_open")
    if conversation.status == "escalated":
        reasons.append("status_escalated")
    if _linked_claims(conversation, claims):
        reasons.append("claim_linked")
    if not _is_test_data(conversation):
        reasons.append("not_test_data")
    return reasons


def redact(conversation: Conversation, stamp: str) -> tuple[Conversation, dict[str, int]]:
    """원문 필드를 `[삭제됨]`으로 바꾼 복사본과, 비운 것의 건수를 돌려준다. 원본은 바꾸지 않는다."""
    out = conversation.model_copy(deep=True)
    out.customer_name = PURGED
    out.subject = PURGED
    if out.subject_display is not None:
        out.subject_display = PURGED
    out.preview = PURGED
    for message in out.messages:
        message.text = PURGED
        if (
            message.display_text is not None
        ):  # 욕설을 가린 글과 순화한 글도 고객 원문에서 나온 것이다
            message.display_text = PURGED
        if message.softened_text is not None:
            message.softened_text = PURGED
        analysis = message.analysis
        if analysis is None:
            continue
        if analysis.classification:
            analysis.classification.intent_ko = PURGED
            analysis.classification.search_query = ""
            analysis.classification.keywords = []  # `pii_masked`는 종류만 있어 남긴다
            if analysis.classification.message_ko:  # 번역과 핵심 요청도 고객 원문에서 나온 것이다
                analysis.classification.message_ko = PURGED
            analysis.classification.request_points_ko = []
        for draft in analysis.drafts or []:
            draft.reply = PURGED
            if draft.reply_ko is not None:
                draft.reply_ko = PURGED
    if out.summary:
        out.summary.summary_ko = PURGED
        out.summary.issue_ko = PURGED
        out.summary.actions_ko = []
        out.summary.next_action_ko = PURGED
        out.summary.escalate_reason_ko = PURGED
        out.summary.tags = []
    activity = 0
    for entry in out.activity:
        if entry.detail or entry.data:
            activity += 1
        entry.detail = ""
        entry.data = {}
    feedback = 0
    if out.feedback and out.feedback.comment:
        out.feedback.comment = ""
        feedback = 1
    notes = 0
    for (
        note
    ) in out.resolution_notes:  # 업무 메모라도 이름·연락처가 적힐 수 있다. 작성자·시각은 남긴다
        written = [n for n in (note.root_cause, note.action_taken, note.result) if n]
        notes += 1 if written else 0
        note.root_cause = PURGED if note.root_cause else note.root_cause
        note.action_taken = PURGED if note.action_taken else note.action_taken
        note.result = PURGED if note.result else note.result
    out.purged_at = stamp
    counts = {"messages": len(out.messages), "activity": activity, "feedback": feedback}
    counts["resolution_notes"] = notes
    return out, counts


def _has_purge_record(store: InquiryStore, inquiry_id: int) -> bool:
    return any(
        r.get("record_type") == "retention_purge" and r.get("inquiry_id") == inquiry_id
        for r in store.privacy_audit
    )


def _already_purged(store: InquiryStore, conversation: Conversation, apply: bool) -> Plan:
    """이미 지운 문의. 지운 뒤 감사 기록 전에 중단됐다면 기록만 보충한다(건수는 알 수 없다)."""
    if _has_purge_record(store, conversation.id):
        return Plan(conversation.id, "already_purged")
    kept = {
        "messages": len(conversation.messages),
        "activity": len(conversation.activity),
        "claims": 0,
    }
    if apply:
        privacy_audit.record_retention_purge(
            store,
            conversation,
            criterion=BACKFILL_CRITERION,
            purged={},
            kept=kept,
            executor_role=EXECUTOR_ROLE,
        )
    return Plan(conversation.id, "already_purged", kept=kept, note="audit_backfill")


def plan_one(
    store: InquiryStore, claims: ClaimStore, inquiry_id: int, apply: bool, stamp: str
) -> Plan:
    conversation = store.get(inquiry_id)
    if conversation is None:
        return Plan(inquiry_id, "not_found")
    if conversation.purged_at:
        return _already_purged(store, conversation, apply)
    reasons = blockers(conversation, claims)
    if reasons:
        return Plan(inquiry_id, "blocked", reasons)
    redacted, purged = redact(conversation, stamp)
    kept = {"messages": len(redacted.messages), "activity": len(redacted.activity), "claims": 0}
    if apply:
        store.replace(redacted)
        privacy_audit.record_retention_purge(
            store,
            redacted,
            criterion=CRITERION,
            purged=purged,
            kept=kept,
            executor_role=EXECUTOR_ROLE,
        )
    return Plan(inquiry_id, "purge", [], purged, kept)


def run(
    store: InquiryStore,
    claims: ClaimStore,
    inquiry_ids: Sequence[int],
    apply: bool = False,
    now: datetime | None = None,
) -> list[Plan]:
    stamp = (now or datetime.now(UTC)).isoformat(timespec="seconds").replace("+00:00", "Z")
    return [plan_one(store, claims, i, apply, stamp) for i in dict.fromkeys(inquiry_ids)]


def _do_purge(
    store: InquiryStore,
    conversation: Conversation,
    apply: bool,
    stamp: str,
    criterion: str,
    role: str,
) -> Plan:
    redacted, purged = redact(conversation, stamp)
    kept = {"messages": len(redacted.messages), "activity": len(redacted.activity), "claims": 0}
    if apply:
        store.replace(redacted)
        privacy_audit.record_retention_purge(
            store, redacted, criterion=criterion, purged=purged, kept=kept, executor_role=role
        )
    return Plan(conversation.id, "purge", [], purged, kept)


def plan_expired(store: InquiryStore, now: datetime, apply: bool, stamp: str) -> list[Plan]:
    """받은 지 3년이 지난 문의의 원문을 지운다. 열린 문의·클레임이 이어진 문의도 예외가 없다.

    개인정보를 3년을 넘겨 들고 있지 않는 것이 목적이라 상태나 클레임을 보지 않는다.
    """
    return [
        _do_purge(store, conversation, apply, stamp, CRITERION_EXPIRED, SYSTEM_ROLE)
        for conversation in list(store.all())
        if not conversation.purged_at and is_expired(conversation, now)
    ]


def plan_customer_request(
    store: InquiryStore,
    claims: ClaimStore,
    request_id: int,
    claimed_ids: set[int],
    apply: bool,
    stamp: str,
) -> list[Plan]:
    """삭제를 요청한 고객(`request_id` 문의의 구매자)의 문의 원문을 지운다.

    지우지 않고 남기는 것(보존 의무): 아직 처리 중인 문의(열림·이관)와 클레임이 이어진 문의.
    남긴 문의에는 `retained_until`(받은 날로부터 3년)을 붙여, 그날 보유기간 삭제가 지운다.
    `claimed_ids`는 다른 프로세스(클레임 서비스)에서 읽은 클레임이 이어진 문의 번호다.
    """
    request = store.get(request_id)
    if request is None:
        return [Plan(request_id, "not_found")]
    if request.user_id is None:  # 구매자를 모르면 누구의 문의인지 묶을 수 없다
        return [Plan(request_id, "blocked", ["no_buyer"])]
    plans: list[Plan] = []
    for conversation in list(store.all()):
        if conversation.user_id != request.user_id:
            continue
        if conversation.purged_at:
            plans.append(Plan(conversation.id, "already_purged"))
            continue
        reasons = []
        if conversation.status == "open":
            reasons.append("status_open")
        if conversation.status == "escalated":
            reasons.append("status_escalated")
        if conversation.id in claimed_ids or _linked_claims(conversation, claims):
            reasons.append("claim_linked")
        if reasons:
            deadline = retention_deadline(conversation)
            until = deadline.date().isoformat() if deadline else None
            plans.append(Plan(conversation.id, "blocked", reasons, retained_until=until))
            continue
        plans.append(
            _do_purge(store, conversation, apply, stamp, CRITERION_CUSTOMER, EXECUTOR_ROLE)
        )
    return plans


def _line(plan: Plan, apply: bool) -> str:
    if plan.action == "purge":
        verb = "지움" if apply else "지울 예정"
        detail = " ".join(f"{k}={v}" for k, v in plan.purged.items())
        return f"문의 {plan.inquiry_id}: {verb} ({detail})"
    if plan.action == "blocked":
        return f"문의 {plan.inquiry_id}: 막힘 ({', '.join(plan.reasons)})"
    if plan.action == "already_purged":
        if plan.note == "audit_backfill":
            verb = "보충함" if apply else "보충할 예정"
            return f"문의 {plan.inquiry_id}: 이미 지웠지만 감사 기록이 없어 {verb}"
        return f"문의 {plan.inquiry_id}: 이미 지움"
    return f"문의 {plan.inquiry_id}: 찾을 수 없음"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m support_agent.retention",
        description="시험·시연 문의의 원문을 [삭제됨]으로 바꾼다. 기본은 dry-run(건수만 센다).",
    )
    parser.add_argument(
        "--inquiry-id", type=int, action="append", required=True, help="지울 문의 ID (여러 번 가능)"
    )
    parser.add_argument("--apply", action="store_true", help="실제로 집행한다 (없으면 dry-run)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if persistence.dsn() is None:
        print(
            f"{persistence.DSN_ENV}가 없다. 별도 프로세스라 서비스의 메모리를 바꿀 수 없다.",
            file=sys.stderr,
        )
        return 2
    store = persistence.build_inquiry_store()
    claims = persistence.build_claim_store()
    plans = run(store, claims, args.inquiry_id, apply=args.apply)
    for plan in plans:
        print(_line(plan, args.apply))
    if not args.apply:
        print("dry-run이다. 집행하려면 --apply를 붙인다.")
    elif any(p.action == "purge" for p in plans):
        print("support 서비스를 다시 시작해야 서비스의 캐시에도 반영된다.")
    return 1 if any(p.action in ("blocked", "not_found") for p in plans) else 0


if __name__ == "__main__":
    raise SystemExit(main())
