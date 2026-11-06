"""보낸 답변과 생성된 초안을 코드 규칙으로 전수 점검한다. 프레임워크에 의존하지 않는 순수 코드다.

설계: docs/notes/support/2026-10-28-knowledge-gap-qa-design.md (4.3)

LLM은 쓰지 않고 점수도 매기지 않는다. 문의마다 규칙 코드와 심각도(`flag`·`info`)만 낸다.
규칙이 못 잡는 것("고객을 제대로 이해했는가")은 사람이 보도록, 규칙에 걸리지 않은 문의 중
일부를 고정 시드로 뽑아 번호만 준다. 모든 기준값은 근거 데이터가 없는 시드다.
점검 결과에는 고객 원문·답변 본문을 싣지 않는다.
"""

from __future__ import annotations

import json
import random
import re
from collections import Counter
from datetime import UTC, datetime, timedelta

from support_agent import kb, languages
from support_agent.insights import parse_time
from support_agent.qa_schemas import QaFinding, QaReport, QaRule, QaSeverity
from support_agent.schemas import Conversation, Message
from support_agent.store import InquiryStore

WEAK_GROUNDING_COVERAGE = 0.1  # 답변이 근거 조각의 2글자 조각을 이만큼도 못 쓰면 의심 (시드)
HEAVY_EDIT_RATIO = 0.5  # 초안과 보낸 답변이 이만큼 이상 다르면 의심 (시드)
SAMPLE_SEED = 441  # 대조군을 같은 입력에 같은 결과로 뽑으려는 고정 시드
SEVERITY: dict[QaRule, QaSeverity] = {
    "guard_hit": "flag",
    "no_evidence_reply": "flag",
    "language_mismatch": "flag",
    # 아래 셋은 근사라 오탐이 있을 수 있어 info로 시작한다
    "weak_grounding": "info",
    "unsupported_number": "info",
    "heavy_edit": "info",
}
NOTES = [
    "점수가 아니라 규칙 코드다. 규칙은 근사이고 오탐·누락이 있으며 기준값은 모두 시드다.",
    "고객을 제대로 이해했는지는 자동으로 보지 않는다. human_review_sample을 사람이 본다.",
]

_NUMBER = r"\d[\d,]*(?:\.\d+)?"
_UNIT = (
    r"(?:영업일|일|개월|달|주일|주|시간|분|원|퍼센트|%|"
    r"business\s+days?|days?|hours?|weeks?|months?|won|円|日|時間|週間|天|个月|元)"
)
# "2~3일"처럼 범위면 앞뒤 숫자를 모두 잡는다
_NUMBER_WITH_UNIT = re.compile(rf"({_NUMBER})(?:\s*[~\-–]\s*({_NUMBER}))?\s*{_UNIT}", re.IGNORECASE)
_ANY_NUMBER = re.compile(_NUMBER)


def _norm(number: str) -> str:
    return number.replace(",", "").rstrip(".")


def numbers_with_unit(text: str) -> set[str]:
    """답변에서 `숫자+단위`로 쓴 숫자(쉼표 제거)를 모은다. 주문번호 같은 단위 없는 숫자는 뺀다."""
    found: set[str] = set()
    for match in _NUMBER_WITH_UNIT.finditer(text):
        found.update(_norm(g) for g in match.groups() if g)
    return found


def _all_numbers(text: str) -> set[str]:
    return {_norm(n) for n in _ANY_NUMBER.findall(text)}


def source_coverage(reply: str, source_texts: list[str]) -> float | None:
    """답변이 가장 잘 맞는 근거 조각의 2글자 조각을 얼마나 담았는지(0~1). 비교할 것이 없으면 None.

    "근거를 실제로 썼는가"의 근사일 뿐 정확하지 않다 (kb 검색과 같은 2글자 조각 겹침).
    """
    reply_grams = kb._grams(reply)
    best: float | None = None
    for text in source_texts:
        grams = kb._grams(text)
        if not grams:
            continue
        coverage = len(reply_grams & grams) / len(grams)
        best = coverage if best is None else max(best, coverage)
    return round(best, 3) if best is not None else None


def _in_window(stamp: str | None, since: datetime, now: datetime) -> bool:
    return stamp is not None and since < parse_time(stamp) <= now


def _previous_analysis(conversation: Conversation, index: int) -> Message | None:
    """이 답변보다 앞선 가장 가까운 고객 메시지 중 분석이 달린 것."""
    for message in reversed(conversation.messages[:index]):
        if message.role == "customer" and message.analysis:
            return message
    return None


def _allowed_numbers(conversation: Conversation, source_texts: list[str]) -> set[str]:
    """답변에 나와도 되는 숫자: 근거 조각, 문의에 이어진 클레임 기록, 고객이 쓴 글에 있는 숫자."""
    allowed: set[str] = set()
    for text in source_texts:
        allowed |= _all_numbers(text)
    allowed |= _all_numbers(json.dumps(conversation.claims, ensure_ascii=False, default=str))
    for message in conversation.messages:
        if message.role == "customer":
            allowed |= _all_numbers(message.text)
    return allowed


def _check_reply(conversation: Conversation, index: int, message: Message) -> list[QaFinding]:
    out: list[QaFinding] = []

    def add(rule: QaRule, **extra: object) -> None:
        out.append(
            QaFinding(
                inquiry_id=conversation.id,
                message_id=message.id,
                rule=rule,
                severity=SEVERITY[rule],
                **extra,  # type: ignore[arg-type]
            )
        )

    customer = _previous_analysis(conversation, index)
    analysis = customer.analysis if customer else None
    sources = (analysis.sources or []) if analysis else []
    source_texts = [f"{s.title} {s.section} {s.text}" for s in sources]

    if analysis is not None and not sources:
        add("no_evidence_reply")

    reply_language = languages.detect(message.text)
    if analysis is not None and analysis.classification:
        customer_language = analysis.classification.language
    elif customer is not None:
        customer_language = languages.detect(customer.text)
    else:
        customer_language = None
    if customer_language is not None and reply_language != languages.reply_language(
        customer_language
    ):
        add("language_mismatch", codes=[f"{customer_language}->{reply_language}"])

    # 한국어 근거 조각과 비교하는 규칙이라 한국어 답변에만 적용한다
    if sources and reply_language == "ko":
        coverage = source_coverage(message.text, source_texts)
        if coverage is not None and coverage < WEAK_GROUNDING_COVERAGE:
            add("weak_grounding", value=coverage, threshold=WEAK_GROUNDING_COVERAGE)

    unsupported = numbers_with_unit(message.text) - _allowed_numbers(conversation, source_texts)
    if unsupported:
        add("unsupported_number", codes=sorted(unsupported, key=lambda n: (len(n), n)))
    return out


def _guard_findings(
    store: InquiryStore,
    by_id: dict[int, Conversation],
    since: datetime,
    now: datetime,
    inquiry_id: int | None,
) -> list[QaFinding]:
    """초안 생성 때의 가드 결과와 가드가 막은 전송 기록을 (문의, 메시지) 단위로 모은다."""
    codes: dict[tuple[int, int | None], set[str]] = {}
    for conversation in by_id.values():
        for message in conversation.messages:
            if message.role != "customer" or not message.analysis:
                continue
            if not _in_window(message.created_at, since, now):
                continue
            validations = [d.validation for d in message.analysis.drafts or []]
            if message.analysis.validation:
                validations.append(message.analysis.validation)
            for validation in validations:
                if validation.findings:
                    codes.setdefault((conversation.id, message.id), set()).update(
                        f.code for f in validation.findings
                    )
    for event in store.draft_events:
        if event.get("result") != "blocked" or not event.get("guard_codes"):
            continue
        conversation = by_id.get(event["inquiry_id"])
        stamp = event.get("occurred_at") or (conversation.updated_at if conversation else None)
        if conversation is None or not _in_window(stamp, since, now):
            continue
        codes.setdefault((conversation.id, event.get("message_id")), set()).update(
            event["guard_codes"]
        )
    return [
        QaFinding(
            inquiry_id=iid,
            message_id=mid,
            rule="guard_hit",
            severity=SEVERITY["guard_hit"],
            codes=sorted(found),
        )
        for (iid, mid), found in codes.items()
        if inquiry_id is None or iid == inquiry_id
    ]


def build_report(
    store: InquiryStore,
    now: datetime | None = None,
    *,
    window_days: int = 7,
    inquiry_id: int | None = None,
    sample_size: int = 5,
) -> QaReport:
    now = now or datetime.now(UTC)
    since = now - timedelta(days=window_days)
    by_id = {
        c.id: c
        for c in store.all()
        # 보유기간으로 원문을 지운 문의는 글자를 읽는 규칙을 돌릴 수 없어 뺀다
        if c.purged_at is None and (inquiry_id is None or c.id == inquiry_id)
    }

    findings = _guard_findings(store, by_id, since, now, inquiry_id)
    checked = 0
    flagged_messages: set[tuple[int, int]] = set()
    seen: set[int] = set()  # 점검 대상이 된 문의 (대조군 후보)
    for conversation in by_id.values():
        for message in conversation.messages:
            if message.role == "customer" and message.analysis:
                if _in_window(message.created_at, since, now):
                    seen.add(conversation.id)
        for index, message in enumerate(conversation.messages):
            if message.role != "agent" or not message.text.strip():
                continue
            if not _in_window(message.created_at, since, now):
                continue
            checked += 1
            seen.add(conversation.id)
            found = _check_reply(conversation, index, message)
            findings.extend(found)
            if any(f.severity == "flag" for f in found):
                flagged_messages.add((conversation.id, message.id))

    for event in store.draft_events:
        ratio = event.get("edit_ratio")
        conversation = by_id.get(event["inquiry_id"])
        stamp = event.get("occurred_at") or (conversation.updated_at if conversation else None)
        if (
            event.get("result") == "edited"
            and ratio is not None
            and ratio >= HEAVY_EDIT_RATIO
            and conversation is not None
            and _in_window(stamp, since, now)
        ):
            seen.add(conversation.id)
            findings.append(
                QaFinding(
                    inquiry_id=conversation.id,
                    message_id=event.get("message_id"),
                    rule="heavy_edit",
                    severity=SEVERITY["heavy_edit"],
                    value=ratio,
                    threshold=HEAVY_EDIT_RATIO,
                )
            )

    findings.sort(key=lambda f: (f.severity != "flag", f.inquiry_id, f.message_id or 0, f.rule))
    # 규칙에 하나도 걸리지 않은 문의에서 고정 시드로 뽑는다 (같은 입력이면 같은 결과)
    clean = sorted(seen - {f.inquiry_id for f in findings})
    sample = (
        sorted(random.Random(SAMPLE_SEED).sample(clean, min(sample_size, len(clean))))
        if inquiry_id is None
        else []
    )
    return QaReport(
        window_days=window_days,
        evaluated_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        replies_checked=checked,
        replies_flagged=len(flagged_messages),
        flagged_rate=round(len(flagged_messages) / checked, 3) if checked else None,
        rule_counts=dict(Counter(f.rule for f in findings)),
        findings=findings,
        human_review_sample=sample,
        notes=NOTES,
    )
