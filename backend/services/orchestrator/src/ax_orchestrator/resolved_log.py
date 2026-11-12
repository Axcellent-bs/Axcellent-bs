"""이벤트가 해결로 끝난 때를 공통 로그에 한 번 남긴다 (`event_published`, incident.resolved).

데이터 AI의 kpi_summary가 이 기록으로 사건별 복구 시간을 센다. payload에 감지 시각(`detected_at`,
이벤트를 연 시각)과 복구 시각(`recovered_at`, 복구 시간의 끝 시각)을 실어 총괄의
`metrics.mttr_seconds`와 같은 값이 나오게 한다.

- 남기는 때: 이벤트가 `resolved`가 되고 복구 시간을 정할 수 있을 때. 처음부터 해결로 닫힐 때와,
  degraded로 닫혔다가 쇼핑몰 복구 신호로 풀릴 때다.
- degraded로 닫히고 복구 신호가 없으면 남기지 않는다.
- 사건마다 한 번만 남긴다(`Incident.resolved_logged`, 저장·복원해도 유지).

이벤트 버스(Redis) 발행과는 따로다. 총괄의 발행 함수는 아직 버스로 보내지 않는다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from ax_agent_template.logging import log_event
from ax_contracts.schemas.common import TENANT_ID_DEFAULT
from ax_orchestrator.models import Incident, iso

RESOLVED_EVENT = "incident.resolved"
SOURCE = "orchestrator"


def _precise(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def log_resolved(inc: Incident) -> bool:
    """해결로 끝난 이벤트면 공통 로그에 한 줄 남긴다. 이번에 남겼으면 True."""
    if inc.resolved_logged or inc.status != "resolved":
        return False
    end = inc.recovery_end()
    if end is None:
        return False
    log_event(
        inc.context_id,
        TENANT_ID_DEFAULT,
        "event_published",
        {
            "event_type": RESOLVED_EVENT,
            "event_id": f"evt_{uuid.uuid4().hex[:12]}",
            # 초 아래까지 싣는다: iso()는 초 단위로 잘라 metrics.mttr_seconds와 1초까지 어긋난다
            "detected_at": _precise(inc.opened_at),
            "recovered_at": _precise(end),
        },
        source=SOURCE,
    )
    inc.resolved_logged = True
    inc.log(f"해결 기록 남김 (복구 {iso(end)})")
    return True


__all__ = ["RESOLVED_EVENT", "log_resolved"]
