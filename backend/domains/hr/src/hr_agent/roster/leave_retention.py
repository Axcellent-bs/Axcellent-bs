"""휴가 기록 보유기간: 기본 3년. 지금은 지울 건수만 센다(지우는 일은 만들지 않았다).

- 보유 일수: `HR_LEAVE_RETENTION_DAYS`(기본 1095 = 3년). **늘리는 것만 된다**: 비었거나
  정수가 아니거나 3년보다 짧거나 상한(36500)을 넘으면 기본값이다(고객지원 클레임과 같은 방식).
- 기준은 휴가 끝 날짜(서울)다. 끝 날짜 + 보유 일수가 오늘 이전이면 지울 대상으로 센다.
- 실제 삭제는 안전 관문에 새 액션을 등록한 뒤 켠다(켜기 전까지는 건수만 센다).
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta

from hr_agent.roster.presence import seoul_date
from hr_agent.services import Services

ENV_DAYS = "HR_LEAVE_RETENTION_DAYS"
DEFAULT_DAYS = 1095  # 3년
MAX_DAYS = 36500


def retention_days() -> int:
    raw = (os.getenv(ENV_DAYS) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_DAYS
    return value if DEFAULT_DAYS <= value <= MAX_DAYS else DEFAULT_DAYS


def expired_count(svc: Services, now: datetime | None = None) -> int:
    """보유기간이 지난 휴가 기록 건수(세기만 한다. 아무것도 바꾸지 않는다)."""
    today = date.fromisoformat(seoul_date(now or svc.clock()))
    cutoff = (today - timedelta(days=retention_days())).isoformat()
    row = svc.store.one("SELECT COUNT(*) AS n FROM leaves WHERE end_date < ?", (cutoff,))
    return int(row["n"]) if row else 0
