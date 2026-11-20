"""안전 처리 로그 대화가 쓰는 사실(facts). 안전 처리 로그 서버가 가진 기록을 그대로 모은다.

- 기준표(thresholds)·등록된 일(registry): 어떤 일에 어떤 기준이 걸려 있는지
- 보류 큐(reviews): 지금 사람 결정을 기다리는 일과 걸린 기준, 최근 결정
- 실행 기록(executions): 최근 판단과 상태·등급별 수
- 연결 보류(connection holds)

숫자·이름은 여기서만 나오고, LLM은 풀어 쓸 뿐이다. 사람 이름(결정한 사람)은 넣지 않는다.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ax_safety.gate import Gate

RECENT_EXECUTIONS = 30
RECENT_DECIDED = 15

GRADES = {
    "A": "바로 실행 (기준 안)",
    "B": "다른 AI(또는 안전 규칙)가 금액을 다시 계산해 맞으면 실행",
    "C": "보류: 사람이 승인해야 실행. 기한 안에 아무도 정하지 않으면 자동 취소",
}

# 기준 이름 (화면 frontend/src/pages/Safety.tsx와 같은 말)
RULE_KO = {
    "coupon_total": "쿠폰 한 번에 주는 총액",
    "coupon_unit": "쿠폰 한 장 금액",
    "coupon_daily": "하루 동안 준 쿠폰 합계",
    "compensation_per_context": "장애 한 건에 준 보상 합계",
    "compensation_per_customer": "한 고객에게 준 보상 합계",
    "refund_single": "환불 한 건 금액",
    "campaign_budget_single": "캠페인 한 건 예산",
    "campaign_budget_daily": "오늘 캠페인 예산 합계",
    "video_duration": "영상 한 건 길이(초)",
    "sns_daily_posts": "오늘 SNS 게시 수",
    "pr_changed_lines": "바꾼 줄 수",
    "pr_changed_files": "바꾼 파일 수",
    "merge_changed_lines": "합칠 때 바꾼 줄 수",
    "merge_changed_files": "합칠 때 바꾼 파일 수",
    "handover_completeness": "인수인계 채움 정도(%)",
    "rollback_multiple_suspects": "의심 배포 수",
}

ACTION_KO = {
    "issue_coupon": "쿠폰 지급",
    "refund_request": "환불",
    "confirm_settlement": "판매자 정산 지급",
    "publish_notice": "공지 올리기",
    "publish_sns": "SNS 게시",
    "set_publish_control": "자동 게시 켜기·끄기",
    "complete_manual_publish": "수동 게시 완료 기록",
    "generate_media": "이미지·영상 만들기",
    "generate_creative_video": "스토리보드 영상 만들기",
    "start_campaign": "캠페인 집행",
    "rollback_deployment": "배포 되돌리기",
    "restart_service": "서비스 다시 켜기",
    "generate_pr": "코드 변경 만들기",
    "merge_pr": "코드 변경 합치기",
    "confirm_spec": "기획서 확정",
    "confirm_handover": "인수인계 확정",
    "confirm_team_budget": "팀 예산 확정",
}


def _rule(rule: Any) -> dict[str, Any]:
    d = rule.model_dump(mode="json")
    out = {k: d.get(k) for k in ("key", "action", "metric", "op", "value", "field")}
    if d.get("when"):
        out["when"] = d["when"]
    if rule.key in RULE_KO:
        out["name"] = RULE_KO[rule.key]
    return out


def _review(review: Any) -> dict[str, Any]:
    d = review.model_dump(mode="json")
    report = d.get("report") or {}
    return {
        "review_id": d["review_id"],
        "action": d["action"],
        "action_name": ACTION_KO.get(d["action"]),
        "requester": d["requester"],
        "status": d["status"],
        "created_at": d["created_at"],
        "expires_at": d["expires_at"],
        "decided_at": d.get("decided_at"),
        "hold_reason": report.get("hold_reason"),
        "computed_amount": report.get("computed_amount"),
        "rules_hit": [
            {k: h.get(k) for k in ("key", "observed", "op", "value") if h.get(k) is not None}
            | ({"name": RULE_KO[h["key"]]} if h.get("key") in RULE_KO else {})
            for h in report.get("rules") or []
        ],
    }


def _execution(rec: Any) -> dict[str, Any]:
    d = rec.model_dump(mode="json")
    dec, req = d["decision"], d["request"]
    return {
        "at": d["created_at"],
        "requester": req["requester"],
        "action": req["action"],
        "action_name": ACTION_KO.get(req["action"]),
        "grade": dec["grade"],
        "status": dec["status"],
        "hold_reason": dec.get("hold_reason"),
        "computed_amount": dec.get("computed_amount"),
        "verifier": dec.get("verifier"),
    }


def collect(gate: Gate) -> dict[str, Any]:
    store = gate.store
    th = gate.thresholds
    reviews = store.list_reviews(None)
    pending = [r for r in reviews if r.status == "pending"]
    decided = sorted(
        (r for r in reviews if r.status != "pending"),
        key=lambda r: r.decided_at or r.created_at,
        reverse=True,
    )[:RECENT_DECIDED]
    executions = sorted(store.list_executions(None), key=lambda r: r.created_at, reverse=True)
    holds = [h.model_dump(mode="json") for h in store.list_connection_holds() if h.hold]
    return {
        "grades": GRADES,
        "thresholds": {
            "version": th.version,
            "verify_timeout_seconds": th.verify_timeout_seconds,
            "review_timeout_seconds": th.review_timeout_seconds,
            "review_timeout_by_action": th.review_timeout_by_action,
            "rules": [_rule(r) for r in th.rules],
        },
        "registry": [
            {
                "action": a.action,
                "action_name": ACTION_KO.get(a.action),
                "owner": a.owner,
                "base_grade": a.base_grade,
                "verifier": a.verifier,
            }
            for a in gate.registry.actions
        ],
        "pending_reviews": [_review(r) for r in pending],
        "recent_decided_reviews": [_review(r) for r in decided],
        "executions": {
            "total": len(executions),
            "by_status": dict(Counter(r.decision.status for r in executions)),
            "by_grade": dict(Counter(r.decision.grade for r in executions)),
            "recent": [_execution(r) for r in executions[:RECENT_EXECUTIONS]],
        },
        "connection_holds": holds,
    }
