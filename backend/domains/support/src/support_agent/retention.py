"""시험·시연 문의의 원문 삭제(치환).

설계: `docs/notes/support/2026-10-28-privacy-audit-evidence-design.md` 4.3.

- 행은 지우지 않는다. 원문 필드를 `[삭제됨]`으로 바꾸고 `purged_at`을 남긴다. 개인정보 감사 기록이
  문의 ID로 묶인 추가 전용 기록이라 행을 지우면 고아가 된다.
- 지금은 운영자가 `--inquiry-id`로 지정하고, 제목이 `[시험]`·`[test]`로 시작하는 문의만 지운다.
  기한이 지난 문의를 자동으로 고르는 보유기간 삭제와 고객 요청 삭제는 이 도구의 범위가 아니다.
- 막는 것: 열린(`open`)·이관(`escalated`) 문의, 클레임이 이어진 문의(법정 보존 대상일 수 있다),
  시험 표시가 없는 문의. 이미 지운 문의는 아무것도 하지 않는다(멱등).
- 남기는 것: 분류·감정·긴급도·상태·시각·메시지 수·피드백 점수·이어진 클레임 ID·감사 기록.
  메시지 본문·활동 상세·피드백 글은 비우고 건수와 시각만 남긴다.
- 기본은 dry-run(건수만 센다). `--apply`로 집행한다. 출력과 감사 기록에는 문의 내용이 없다.
- 외부에 열린 삭제 경로는 만들지 않는다. 운영자가 CLI로만 실행한다
  (`python -m support_agent.retention --inquiry-id 5 [--apply]`).
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
EXECUTOR_ROLE = "operator"  # 사람 이름이 아니라 역할 코드. 감사 기록에도 이것만 남긴다


@dataclass
class Plan:
    inquiry_id: int
    action: str  # purge | already_purged | blocked | not_found
    reasons: list[str] = field(default_factory=list)
    purged: dict[str, int] = field(default_factory=dict)
    kept: dict[str, int] = field(default_factory=dict)


def _is_test_data(conversation: Conversation) -> bool:
    return conversation.subject.strip().lower().startswith(tuple(m.lower() for m in TEST_MARKERS))


def _linked_claims(conversation: Conversation, claims: ClaimStore) -> list[int]:
    """이 문의에 이어진 클레임 ID. 활동 기록의 `claim:<id>` 참조와 같은 주문의 클레임을 본다."""
    ids = {
        int(entry.ref.removeprefix("claim:"))
        for entry in conversation.activity
        if entry.ref and entry.ref.startswith("claim:") and entry.ref[6:].isdigit()
    }
    ids.update(int(c["id"]) for c in conversation.claims if str(c.get("id", "")).isdigit())
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
    out.preview = PURGED
    for message in out.messages:
        message.text = PURGED
        analysis = message.analysis
        if analysis is None:
            continue
        if analysis.classification:
            analysis.classification.intent_ko = PURGED
            analysis.classification.search_query = ""
            analysis.classification.keywords = []  # `pii_masked`는 종류만 있어 남긴다
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
    out.purged_at = stamp
    return out, {"messages": len(out.messages), "activity": activity, "feedback": feedback}


def plan_one(
    store: InquiryStore, claims: ClaimStore, inquiry_id: int, apply: bool, stamp: str
) -> Plan:
    conversation = store.get(inquiry_id)
    if conversation is None:
        return Plan(inquiry_id, "not_found")
    if conversation.purged_at:
        return Plan(inquiry_id, "already_purged")
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


def _line(plan: Plan, apply: bool) -> str:
    if plan.action == "purge":
        verb = "지움" if apply else "지울 예정"
        detail = " ".join(f"{k}={v}" for k, v in plan.purged.items())
        return f"문의 {plan.inquiry_id}: {verb} ({detail})"
    if plan.action == "blocked":
        return f"문의 {plan.inquiry_id}: 막힘 ({', '.join(plan.reasons)})"
    if plan.action == "already_purged":
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
