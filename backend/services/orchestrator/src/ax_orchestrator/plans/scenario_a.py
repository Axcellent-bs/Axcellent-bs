"""시나리오 A: 결제 API 장애 (docs/planning.md 5장, docs/notes/scn-a 정리).

- 영향 고객 일괄 보상: 데이터 영향 분석의 고객 목록을 총괄이 고객지원
  `request_incident_compensation`에 한 번에 넘긴다(문의를 안 낸 고객 포함).
  문의를 낸 고객의 보상은 고객지원이 직통으로 처리한다(#228).
- 갈림길: 원인 미확정이면 롤백하지 않고 이관 큐로, 복구 확인이 실패하면
  정상화 공지 없이 이관 큐로 돌린다.
- 원인 추정: 영향 분석 다음에 데이터 `root_cause_hypotheses`로 원인 후보를 받는다(사실만 근거).
  확신도 high 후보가 없거나 원인 추정을 받지 못했으면 원인 확인 업무를 기획개발 `create_ticket`으로
  넘긴다. 그 서비스가 꺼져 있으면 업무 요청 라우터가 사람 할 일함(받은 요청)으로 넘긴다.
- 이벤트 후: 데이터 처리 시간 요약(`kpi_summary`)을 요청한다.
- 반복 장애: 이벤트가 열리면 인사 `find_similar_cases`로 비슷한 과거 이벤트와 그때 해결 내용을 찾아
  보여 준다(참고만, 다른 단계를 바꾸지 않는다). 이벤트 기록 뒤 인사 `record_resolution`으로 이번
  해결 내용을 카드에 남겨 다음 이벤트가 찾게 한다. 값이 없으면 지어내지 않고 비운다.
- 재무는 이벤트 중에 부르지 않는다(멘토 피드백, SCN-A). 장애 때 나간 쿠폰·환불은 재무 장부에
  기록되고, 비용은 평상시 결산·사후 감사에서 다룬다.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from ax_orchestrator.models import Incident, Plan, Step, iso, now
from ax_orchestrator.timeline import past_hint

# 고객지원 `request_incident_compensation`이 한 호출에 받는 구매자 수 상한
COMPENSATION_BATCH = 50


def _window(inc: Incident) -> dict[str, Any]:
    """영향 분석 구간. 신호가 장애 시작 시각(since)을 주면 그때부터 센다.

    없으면 예전처럼 이벤트가 열린 시각 앞 15분부터. 앞 장애(이미 지난 다른 이벤트)의 실패 주문이
    섞이지 않게 하려는 것이다.
    """
    start = inc.since if inc.since is not None else inc.opened_at - timedelta(minutes=15)
    return {
        "window_from": iso(start),
        "window_to": iso(inc.opened_at + timedelta(minutes=15)),
    }


def _out(inc: Incident, step_id: str) -> dict[str, Any]:
    return inc.outputs.get(step_id, {})


def _diagnose(inc: Incident) -> dict[str, Any]:
    return {"component": inc.component}


def _impact(inc: Incident) -> dict[str, Any]:
    return {**_window(inc), "scope": "payment"}


def _rollback(inc: Incident) -> dict[str, Any]:
    suspect = (_out(inc, "diagnose").get("suspects") or [{}])[0]
    return {
        "service": suspect.get("service"),
        "from_version": suspect.get("version"),
        "to_version": suspect.get("previous_version"),
        "release_fault_injection": True,
    }


def _related_prs(inc: Incident) -> dict[str, Any]:
    return {**_window(inc), "component": inc.component}


def _link(inc: Incident) -> dict[str, Any]:
    prs = [c.get("pr_number") for c in _out(inc, "related_prs").get("candidates", [])]
    return {"incident_context_id": inc.context_id, "pr_numbers": [p for p in prs if p]}


def _notice(inc: Incident) -> dict[str, Any]:
    # 실제 marketing `DraftNoticeInput`: 사실만 넘기고 문장은 마케팅이 만든다
    return {
        "kind": "incident_opened",
        "facts": {"component": inc.component, "opened_at": iso(inc.opened_at)},
    }


def _publish_notice(inc: Incident) -> dict[str, Any]:
    return {"draft_id": _out(inc, "notice").get("draft_id")}


def _recovery(inc: Incident) -> dict[str, Any]:
    return {"component": inc.component, "since": iso(inc.opened_at)}


def _recovered_notice(inc: Incident) -> dict[str, Any]:
    return {"draft_id": _out(inc, "recovered_draft").get("draft_id")}


def _recovered_draft(inc: Incident) -> dict[str, Any]:
    return {
        "kind": "incident_resolved",
        "facts": {
            "component": inc.component,
            "opened_at": iso(inc.opened_at),
            "resolved_at": iso(now()),
        },
    }


RECOVERY_TIMEOUT_SECONDS = 180.0  # 인프라가 30초 간격 최대 5회 다시 확인하는 시간을 기다려 준다


def _rollback_gate(inc: Incident) -> tuple[str, str, str] | None:
    """원인이 정해지지 않았으면 롤백하지 않는다. 사람이 원인을 확인하게 이관 큐로 넘긴다."""
    diagnosis = _out(inc, "diagnose")
    if diagnosis.get("status") == "suspected" and diagnosis.get("suspects"):
        return None
    note = "원인 미확정 — 롤백하지 않고 사람이 원인을 확인하도록 이관 큐로 넘김"
    hint = past_hint(inc)  # 같은 유형의 과거 이벤트가 있으면 사람이 먼저 볼 수 있게 붙인다(참고용)
    return ("transfer_queued", "no_rollback_target", f"{note}. {hint}" if hint else note)


def _recovery_verdict(output: dict[str, Any]) -> tuple[str, str] | None:
    """서버·결제 실패율이 정상으로 돌아오지 않았으면(인프라가 재시도한 뒤에도) 사람이 판단한다."""
    if output.get("recovered") is False:
        return (
            "not_recovered",
            "복구 확인 실패 — 정상화 공지 없이 사람이 판단하도록 이관 큐로 넘김",
        )
    return None


def _compensation(inc: Incident) -> dict[str, Any]:
    # 고객 목록은 데이터 영향 분석 결과 그대로(가명 ID). 단가·건수 상한은 고객지원 정책표가 정한다.
    return {
        "buyer_ids": list(_out(inc, "impact").get("affected_customer_ids") or []),
        "reason_code": "payment_failure",
        "impact_ref": f"{inc.context_id}:impact",
    }


def _merge_compensation(outputs: list[dict[str, Any]]) -> dict[str, Any]:
    """50명씩 나눠 부른 결과를 하나로 합친다. 숫자는 고객지원이 준 값을 더하기만 한다."""
    merged = {**outputs[0]}
    merged["requested"] = sum(o.get("requested", 0) for o in outputs)
    merged["results"] = [r for o in outputs for r in o.get("results", [])]
    merged["stopped_early"] = any(o.get("stopped_early") for o in outputs)
    merged["notes"] = [n for o in outputs for n in o.get("notes", [])]
    return merged


def _compensation_gate(inc: Incident) -> tuple[str, str, str] | None:
    if _out(inc, "impact").get("affected_customer_ids"):
        return None
    return ("skipped", "no_affected_customers", "영향 고객 목록이 없어 보상 요청 없음")


def _root_cause(inc: Incident) -> dict[str, Any]:
    # 이상 구간은 영향 분석과 같다. 지표는 결제 장애라 결제 실패율이다
    window = _window(inc)
    return {
        "context_id": inc.context_id,
        "window_from": window["window_from"],
        "window_to": window["window_to"],
        "metric": "payment_fail_rate",
        "component": inc.component,
    }


# 데이터분석 AI가 원인 후보를 낼 수 있는 업무 지표는 결제 실패율 등뿐이다(응답 지연은 없다)
UNSUPPORTED_BY_DATA = {"p95_latency_ms": "응답 지연"}


def _root_cause_gate(inc: Incident) -> tuple[str, str, str] | None:
    """데이터분석 AI가 다루지 못하는 지표면 묻지 않는다(묻으면 결제 실패율 후보가 온다)."""
    name = UNSUPPORTED_BY_DATA.get(str(inc.signal.get("metric_code")))
    if name is None:
        return None
    return (
        "skipped",
        "metric_not_supported",
        f"데이터분석 AI가 {name} 지표는 아직 분석하지 못해 원인 후보를 받지 않음",
    )


def _cause_check_gate(inc: Incident) -> tuple[str, str, str] | None:
    """원인 후보에 확신도 high가 있으면 원인 확인 업무를 넘기지 않는다."""
    found = _out(inc, "root_cause")
    if found and found.get("needs_confirmation") is False:
        return ("skipped", "cause_confident", "확신도 높은 원인 후보가 있어 원인 확인 요청 없음")
    return None


def _cause_check(inc: Incident) -> dict[str, Any]:
    """원인 확인 요청. 문장은 데이터가 준 요약·후보 설명을 그대로 옮기고 지어내지 않는다."""
    found = _out(inc, "root_cause")
    facts = {f.get("fact_id"): f for f in found.get("facts") or []}
    hypotheses = found.get("hypotheses") or []
    if found:
        lines = [str(found.get("summary") or "")]
        lines += [f"후보({h.get('confidence')}): {h.get('explanation')}" for h in hypotheses]
    else:
        lines = ["원인 추정을 받지 못함 (확인할 수 없음)"]
    hint = past_hint(inc)
    if hint:
        lines.append(hint)
    refs = [
        facts[i]["ref"]
        for h in hypotheses
        for i in h.get("fact_ids") or []
        if i in facts and facts[i].get("ref") and facts[i].get("kind") != "anomaly"
    ]
    return {
        "title": f"{inc.component} 장애 원인 확인",
        "evidence": " / ".join(line for line in lines if line),
        "source_ref": inc.context_id,
        "evidence_ids": list(dict.fromkeys(refs)),
    }


def _kpi(inc: Incident) -> dict[str, Any]:
    return {"context_ids": [inc.context_id]}


def _similar(inc: Incident) -> dict[str, Any]:
    # 이벤트가 열린 시점에 아는 것(제목)만으로 찾는다. 해결 내용이 있는 사례를 우선 보여 준다.
    return {"query": inc.title, "event_type": inc.event_type, "limit": 3}


def _resolution(inc: Incident) -> dict[str, Any]:
    """이번 이벤트의 해결 내용. 단계 결과에 있는 사실만 옮기고 없으면 비운다."""
    recovered = inc.steps["recovery"].status == "completed"
    rollback = inc.steps["rollback"]
    action = None
    if rollback.status == "completed":
        out = _out(inc, "rollback")
        service = out.get("service") or "배포"
        before, after = out.get("from_version"), out.get("to_version")
        action = f"{service} 롤백" + (f" ({before} → {after})" if before and after else "")
    found = _out(inc, "root_cause")
    top = next((h for h in found.get("hypotheses") or [] if h.get("confidence") == "high"), None)
    minutes = max(int((now() - inc.opened_at).total_seconds() // 60), 0)
    return {
        "context_id": inc.context_id,
        "outcome": "resolved" if recovered else "unresolved",
        "root_cause": str(top["explanation"]) if top and top.get("explanation") else None,
        "action": action,
        "minutes_to_resolve": minutes if recovered else None,
    }


def scenario_a() -> Plan:
    return Plan(
        name="scenario_a",
        steps=[
            Step("diagnose", "diagnose_incident", "원인 진단 (infra)", build=_diagnose),
            Step(
                "similar_cases",
                "find_similar_cases",
                "비슷한 과거 이벤트 찾기 (hr)",
                build=_similar,
                required=False,
            ),
            Step("impact", "impact_analysis", "영향 분석 (data)", build=_impact),
            Step(
                "root_cause",
                "root_cause_hypotheses",
                "원인 후보 추정 (data)",
                after=("impact",),
                build=_root_cause,
                gate=_root_cause_gate,
                required=False,
            ),
            Step(
                "cause_check",
                "create_ticket",
                "원인 확인 요청 (planning)",
                also_after=("root_cause",),
                build=_cause_check,
                gate=_cause_check_gate,
                required=False,
            ),
            Step(
                "rollback",
                "rollback_deployment",
                "원인 배포 롤백 (B)",
                after=("diagnose", "impact"),
                build=_rollback,
                gate=_rollback_gate,
            ),
            Step(
                "related_prs",
                "find_related_prs",
                "관련 PR 찾기 (planning)",
                after=("diagnose", "impact"),
                build=_related_prs,
                required=False,
            ),
            Step(
                "link_incident",
                "link_incident",
                "이벤트에 PR 연결 (planning)",
                after=("related_prs",),
                build=_link,
                required=False,
            ),
            Step(
                "notice",
                "draft_notice",
                "점검 공지 초안 (marketing)",
                after=("diagnose", "impact"),
                build=_notice,
            ),
            Step(
                "publish_notice",
                "publish_notice",
                "점검 공지 게시 (marketing)",
                after=("notice",),
                build=_publish_notice,
                keep={"notice_id": "notice_id"},
            ),
            Step(
                "compensation",
                "request_incident_compensation",
                "영향 고객 일괄 보상 (support)",
                after=("impact",),
                build=_compensation,
                gate=_compensation_gate,
                split=("buyer_ids", COMPENSATION_BATCH),  # 고객지원이 한 번에 50명까지 받는다
                merge=_merge_compensation,
                required=False,
            ),
            Step(
                "recovery",
                "verify_recovery",
                "복구 검증 (infra)",
                after=("rollback",),
                build=_recovery,
                verdict=_recovery_verdict,
                timeout=RECOVERY_TIMEOUT_SECONDS,
            ),
            Step(
                "recovered_draft",
                "draft_notice",
                "정상화 공지 초안 (marketing)",
                after=("recovery",),
                build=_recovered_draft,
            ),
            Step(
                "recovered_notice",
                "publish_notice",
                "정상화 공지 (marketing)",
                after=("recovered_draft",),
                build=_recovered_notice,
                keep={"notice_id": "recovered_notice_id"},
            ),
            Step(
                "kpi",
                "kpi_summary",
                "처리 시간 요약 (data)",
                also_after=("recovered_notice",),
                build=_kpi,
                required=False,
            ),
            Step(
                "record",
                "record_case",
                "이벤트 기록 (hr)",
                also_after=(
                    "recovered_notice",
                    "link_incident",
                    "compensation",
                    "kpi",
                    "root_cause",
                ),
                kind="record",
            ),
            Step(
                "resolution",
                "record_resolution",
                "해결 내용 기록 (hr)",
                also_after=("record",),
                build=_resolution,
                required=False,
            ),
        ],
    )
