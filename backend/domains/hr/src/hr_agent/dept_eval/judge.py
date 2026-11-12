"""단계별 판정: 시간이 아닌 상태를 먼저 가르고, 일한 시간만 기준과 비교한다. LLM은 쓰지 않는다.

판정 값:
- normal: 일한 시간이 기준 이하이고 재시도도 기준 이내. 기준 시간은 단계의 약속 시간(총괄이 같이
  보낸 `promised_seconds`, 기준값 v2)이고, 약속이 없으면 기본값(30초)이다
- check: 기준을 넘었거나 재시도가 있었다 (시도 횟수를 같이 남긴다)
- no_record: 시간 기록이 없다(건너뜀·시작이나 끝 시각을 모름). 지연으로 세지 않는다
- blocked: 실패했거나 사람이 거절했다 (실패·막힘)
- handed_off: 스스로 끝내지 않고 사람이 보도록 넘겼다(원인을 확정하지 못함 등). 설계된 동작이라
  실패도 지연도 아니다. 사람 할 일함에서 처리를 기다리는 중인 단계도 여기에 든다
- approval_expired: 승인을 기다리다 시간이 지나 자동 거절됐다
- in_progress: 아직 끝나지 않아 판정을 미룬다
외부 대기(부서 안에서 쇼핑몰 등을 기다린 시간)는 총괄이 구분하지 못해 v1에서는 일한 시간에 그대로
들어간다.
"""

from __future__ import annotations

from typing import Any

from hr_agent.dept_eval.criteria import Criteria
from hr_agent.dept_eval.stages import stage_of

REVIEW_TIMEOUT = "review_timeout"
BLOCKED_STATUSES = ("failed", "rejected_by_human")
HANDED_OFF_STATUSES = ("transfer_queued", "waiting_human")
OPEN_STATUSES = ("pending", "running")


def judge_step(timing: dict[str, Any], criteria: Criteria) -> dict[str, Any] | None:
    """단계 하나의 판정. 평가 대상이 아니면 None."""
    step_id = str(timing.get("step_id", ""))
    stage = stage_of(step_id)
    if stage is None:
        return None
    status = str(timing.get("status", ""))
    work = timing.get("work_seconds")
    attempts = int(timing.get("attempts") or 0)
    reason = timing.get("reason_code")
    rule = criteria.rule_for(stage.group)
    promised = timing.get("promised_seconds")
    has_promise = rule.use_promised and isinstance(promised, int | float) and promised > 0
    limit = float(promised) if has_promise else rule.normal_max_seconds

    if reason == REVIEW_TIMEOUT:
        verdict = "approval_expired"
    elif status in HANDED_OFF_STATUSES:
        verdict = "handed_off"
    elif status in BLOCKED_STATUSES:
        verdict = "blocked"
    elif status in OPEN_STATUSES:
        verdict = "in_progress"
    elif status != "completed" or work is None:
        verdict = "no_record"
    elif work <= limit and attempts <= rule.normal_max_attempts:
        verdict = "normal"
    else:
        verdict = "check"
    return {
        "step_id": step_id,
        "dept": stage.dept,
        "group": stage.group,
        "verdict": verdict,
        "status": status,
        "work_seconds": work,
        "attempts": attempts,
        "limit_seconds": limit,
        # 기준 시간이 단계의 약속에서 왔는지(True), 기본값에서 왔는지(False)
        "promised": has_promise,
        "reason_code": reason,
    }


def judge_timings(timings: list[dict[str, Any]], criteria: Criteria) -> dict[str, Any]:
    """이벤트 하나의 단계 시간 목록 → 판정 묶음. 기준값 버전을 같이 담는다."""
    items = [j for t in timings if (j := judge_step(t, criteria)) is not None]
    return {"criteria_version": criteria.version, "items": items}
