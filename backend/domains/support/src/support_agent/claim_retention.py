"""클레임 기록의 보유기간 삭제. 팀 기준: 개인정보처리방침 제4조 ⑥ 클레임 기록 5년.

문의 원문(3년, 고객지원 결정)과 달리 클레임 기록은 **팀 기준**을 따른다(방침과
shop-api `retention.py`와 같은 방식).

- 보유 일수: `SUPPORT_CLAIM_RETENTION_DAYS`(기본 1825 = 5년, 방침 제4조 ⑥). **늘리는 것만 된다**:
  `DEFAULT_DAYS <= 값 <= MAX_DAYS`(36500)일 때만 쓰고, 비었거나 정수가 아니거나 5년보다 짧거나
  상한을 넘으면 기본값이다. 짧은 값을 받으면 5년이 안 된 클레임까지 A 등급으로 바로 지워지는데
  안전 관문은 건수(`count`)만 봐서 막지 못하기 때문이다. 기준은 클레임의 `created_at`이다.
- 삭제는 기본 **dry-run**(건수만 센다). 실제 삭제는 사람이 `SUPPORT_CLAIM_RETENTION_APPLY=1`로 켠다
  (shop-api `SHOP_RETENTION_APPLY`와 같다). 켜기 전에는 skill이 지울 건수만 알린다.
- 행은 지우지 않는다. 사람 이름(`customer`)·상담원 결정 메모·검수 메모·내력의 글을 `[삭제됨]`으로
  바꾸고 `purged_at`을 남긴다. 금액·판정·상태·주문·구매자 가명 ID는 남긴다(통계·감사).
  내력에는 `retention_purge`(기준·보유 일수만, 내용 없음)를 한 줄 더한다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from support_agent.claim_schemas import Claim, ClaimAudit
from support_agent.claims import ClaimStore

ENV_DAYS = "SUPPORT_CLAIM_RETENTION_DAYS"
ENV_APPLY = "SUPPORT_CLAIM_RETENTION_APPLY"
DEFAULT_DAYS = 1825  # 5년 (방침 제4조 ⑥)
MAX_DAYS = 36500  # 100년. 이보다 크면 잘못된 값으로 본다
PURGED = "[삭제됨]"
CRITERION = "claim_retention"
SYSTEM_ACTOR = "system"


def retention_days() -> int:
    raw = (os.getenv(ENV_DAYS) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_DAYS
    return value if DEFAULT_DAYS <= value <= MAX_DAYS else DEFAULT_DAYS


def apply_enabled() -> bool:
    """사람이 켰을 때만 실제로 지운다. 켜지 않으면 건수만 센다."""
    return (os.getenv(ENV_APPLY) or "").strip() == "1"


def _parse(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def deadline(claim: Claim, days: int | None = None) -> datetime | None:
    created = _parse(claim.created_at)
    if created is None:
        return None
    return created + timedelta(days=days if days is not None else retention_days())


def is_expired(claim: Claim, now: datetime, days: int | None = None) -> bool:
    limit = deadline(claim, days)
    return limit is not None and now >= limit


@dataclass
class ClaimPlan:
    claim_id: int
    action: str  # purge | already_purged
    purged: dict[str, int] = field(default_factory=dict)


def redact(claim: Claim, stamp: str, days: int) -> tuple[Claim, dict[str, int]]:
    """글·이름을 `[삭제됨]`으로 바꾼 복사본과 바꾼 건수를 돌려준다. 원본은 바꾸지 않는다."""
    out = claim.model_copy(deep=True)
    out.customer = PURGED
    notes = 0
    for name in ("agent_note", "inspection_note"):
        if getattr(out, name):
            setattr(out, name, PURGED)
            notes += 1
    details = 0
    for entry in out.audit:  # 내력의 글(메모 등)만 비운다. 숫자·참거짓은 판정 근거라 남긴다
        for key, value in list(entry.detail.items()):
            if isinstance(value, str) and value:
                entry.detail[key] = PURGED
                details += 1
    out.audit.append(
        ClaimAudit(
            actor=SYSTEM_ACTOR,
            action="retention_purge",
            detail={"criterion": CRITERION, "retention_days": days},
            at=stamp,
        )
    )
    out.purged_at = stamp
    out.updated_at = stamp
    return out, {"customer": 1, "notes": notes, "audit_details": details}


def plan_expired(
    store: ClaimStore, now: datetime, apply: bool, stamp: str, days: int | None = None
) -> list[ClaimPlan]:
    """보유 일수가 지난 클레임을 모은다. `apply`면 `[삭제됨]`으로 바꿔 저장소에 반영한다."""
    days = days if days is not None else retention_days()
    plans: list[ClaimPlan] = []
    for claim in store.list():
        if claim.purged_at or not is_expired(claim, now, days):
            continue
        redacted, counts = redact(claim, stamp, days)
        if apply:
            store.replace(redacted)
        plans.append(ClaimPlan(claim.id, "purge", counts))
    return plans
