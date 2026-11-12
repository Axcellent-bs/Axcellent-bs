"""시나리오 A: 결제 API 장애 (docs/planning.md 5장, docs/notes/scn-a 정리).

- 영향 고객 일괄 보상: 데이터 영향 분석의 고객 목록을 총괄이 고객지원
  `request_incident_compensation`에 한 번에 넘긴다(문의를 안 낸 고객 포함).
  문의를 낸 고객의 보상은 고객지원이 직통으로 처리한다(#228).
- 갈림길: 원인 미확정이면 롤백하지 않고 이관 큐로, 복구 확인이 실패하면
  정상화 공지 없이 이관 큐로 돌린다.
- 원인 추정: 영향 분석 다음에 데이터 `root_cause_hypotheses`로 원인 후보를 받는다(사실만 근거).
  확신도 high 후보가 없거나 원인 추정을 받지 못했으면 원인 확인 업무를 기획 `create_ticket`으로
  넘긴다. 그 서비스가 꺼져 있으면 업무 요청 라우터가 사람 할 일함(받은 요청)으로 넘긴다.
- 이벤트 후: 데이터 처리 시간 요약(`kpi_summary`)을 요청한다.
- 반복 장애: 이벤트가 열리면 인사 `find_similar_cases`로 비슷한 과거 이벤트와 그때 해결 내용을 찾아
  보여 준다(참고만, 다른 단계를 바꾸지 않는다). 이벤트 기록 뒤 인사 `record_resolution`으로 이번
  해결 내용을 카드에 남겨 다음 이벤트가 찾게 한다. 값이 없으면 지어내지 않고 비운다.
- 재무 장부 기록: 보상·복구 확인이 끝나면 재무 `record_incident_finance`로 이 이벤트를 장부에 남긴다
  (구간·부품, 데이터 영향 숫자 그대로). 재무는 장부의 보상 쿠폰과 구간 안 환불 합계를 더해 답한다.
  재무는 기록만 하고 보상·환불을 결정하지 않는다. 재무가 꺼져 있거나 실패해도 다른 단계는 그대로다.
  장애 때 나간 쿠폰·환불 하나하나는 지금처럼 이벤트로 재무 장부에 들어간다.
  문의가 닫히면 `support_resolved` plan에서 총괄이 재무를 불러 장부에 기록한다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

from ax_orchestrator.draft import kst_text
from ax_orchestrator.models import WAITING_RECOVERY, Incident, Plan, Step, iso, now
from ax_orchestrator.naming import plain_terms
from ax_orchestrator.recommend import recommend
from ax_orchestrator.timeline import past_hint

# 고객지원 `request_incident_compensation`이 한 호출에 받는 구매자 수 상한
COMPENSATION_BATCH = 50


def _window(inc: Incident) -> dict[str, Any]:
    """영향 분석 구간. 신호가 장애 시작 시각(since)을 주면 그때부터 센다.

    없으면 예전처럼 이벤트가 열린 시각 앞 15분부터. 앞 장애(이미 지난 다른 이벤트)의 실패 주문이
    섞이지 않게 하려는 것이다.
    """
    start, end = inc.impact_window()
    return {"window_from": iso(start), "window_to": iso(end)}


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


def fixed_at(inc: Incident) -> datetime:
    """복구 확인을 셀 시작 시각 = 조치가 끝난 시각 (#909).

    사건 열린 시각부터 세면 장애 동안의 실패가 늘 섞여 복구로 판정되지 않는다. 조치 순서대로
    과거 처리 재사용(release_fault) 완료, 롤백 완료, 사람이 장애를 해제한 시각(`shop.recovered`의
    `recovery=fault_released`를 받은 시각)이고, 셋 다 없으면 지금처럼 사건 열린 시각이다.
    """
    past = inc.steps.get("past_fix")
    if _past_fix_done(inc) and past is not None and past.finished_at is not None:
        return past.finished_at
    rollback = inc.steps.get("rollback")
    if rollback is not None and rollback.status == "completed" and rollback.finished_at:
        return rollback.finished_at
    if _human_released(inc) and inc.recovered_at is not None:
        return inc.recovered_at
    return inc.opened_at


def _recovery(inc: Incident) -> dict[str, Any]:
    return {"component": inc.component, "since": iso(fixed_at(inc))}


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


def _end_notice(inc: Incident) -> dict[str, Any]:
    return {"reason": "recovery_not_announced"}


def _end_notice_gate(inc: Incident) -> tuple[str, str, str] | None:
    """정상화 공지 없이 대응이 끝났는데 쇼핑몰이 복구됐으면, 남은 장애 배너를 내린다.

    - 정상화 공지가 나갔으면 마케팅이 그때 장애 배너를 내렸다(정상화 배너까지 내리면 안 된다).
    - 올린 장애 공지가 없으면 내릴 것이 없다.
    - 복구 신호를 아직 못 받았으면 장애가 계속 중일 수 있어 내리지 않는다. 신호가 이벤트가
      닫힌 뒤 오면 총괄이 이 단계를 다시 판단한다(`rerun_on_recovery`).
    """
    recovered = inc.steps.get("recovered_notice")
    if (recovered is not None and recovered.status == "completed") or inc.shared.get(
        "recovered_notice_id"
    ):
        return ("skipped", "notice_already_ended", "정상화 공지로 장애 배너를 이미 내림")
    if not inc.shared.get("notice_id"):
        return ("skipped", "no_incident_notice", "올린 장애 공지가 없음")
    if inc.recovered_at is None:
        return ("skipped", WAITING_RECOVERY, "정상 복귀 신호를 받으면 장애 배너를 내림")
    return None


# 같은 유형 과거 장애의 처리 방식을 다시 할 때 필요한 유사도 하한
PAST_FIX_MIN_SIMILARITY = 0.85
# 다시 할 수 있는 처리 방식 → 인프라 skill. 이전 버전으로 되돌리기는 원인 배포가 있어야 해서
# 진단이 원인 배포를 찾은 경우(아래 rollback 단계)에만 한다
REPLAYABLE = {"release_fault"}


def _past_fix_gate(inc: Incident) -> tuple[str, str, str] | None:
    """같은 유형 과거 장애가 같은 처리 방식으로 풀렸을 때만 그 방식을 바로 다시 한다.

    원인 배포를 찾았으면 지금처럼 되돌리기로 처리하고, 과거 사례가 없거나 처리 방식을 다시 할 수
    없거나 덜 비슷하면 하지 않는다(사람에게 넘기는 글에는 과거 조치가 참고로 붙는다).
    """
    if _out(inc, "diagnose").get("status") == "suspected":
        return ("skipped", "cause_found", "원인 배포를 찾아 되돌리기로 처리")
    rec = recommend(inc.outputs.get("similar_cases")) or {}
    if rec.get("status") != "found":
        return ("skipped", "no_past_case", "같은 유형 과거 장애의 해결 기록 없음")
    if rec.get("action_code") not in REPLAYABLE:
        return ("skipped", "not_replayable", "과거 처리 방식을 AI가 다시 할 수 없음")
    if (rec.get("similarity") or 0) < PAST_FIX_MIN_SIMILARITY:
        pct = round((rec.get("similarity") or 0) * 100)
        return ("skipped", "not_similar_enough", f"과거 장애와 덜 비슷함({pct}%)")
    return None


def _past_fix(inc: Incident) -> dict[str, Any]:
    rec = recommend(inc.outputs.get("similar_cases")) or {}
    return {
        "component": inc.component,
        "past_case_id": rec.get("card_id"),
        "similarity": rec.get("similarity") or 0.0,
    }


def _past_fix_done(inc: Incident) -> bool:
    run = inc.steps.get("past_fix")
    return (
        run is not None
        and run.status == "completed"
        and bool(_out(inc, "past_fix").get("fault_released"))
    )


def _rollback_gate(inc: Incident) -> tuple[str, str, str] | None:
    """원인이 정해지지 않았으면 롤백하지 않는다. 사람이 원인을 확인하게 이관 큐로 넘긴다.

    같은 유형 과거 장애의 처리 방식으로 이미 풀었으면 되돌리지 않는다.
    """
    if _past_fix_done(inc):
        return (
            "skipped",
            "past_fix_applied",
            "과거 같은 장애의 처리 방식으로 처리해 되돌리지 않음",
        )
    diagnosis = _out(inc, "diagnose")
    if diagnosis.get("status") == "suspected" and diagnosis.get("suspects"):
        return None
    note = "원인을 아직 모르므로 되돌리지 않고, 사람이 원인을 확인하도록 넘김"
    hint = past_hint(inc)  # 같은 유형의 과거 이벤트가 있으면 사람이 먼저 볼 수 있게 붙인다(참고용)
    return ("transfer_queued", "no_rollback_target", f"{note}. {hint}" if hint else note)


def _human_released(inc: Incident) -> bool:
    """사람이 장애 상태를 해제했다는 신호(shop.recovered, recovery=fault_released)를 받았는지."""
    how = inc.shared.get("recovery")
    return (
        inc.recovered_at is not None
        and isinstance(how, dict)
        and how.get("recovery") == "fault_released"
    )


def _handed_to_person(inc: Incident) -> bool:
    """롤백을 사람에게 넘겼거나(이관 큐) 사람이 거절했다 = 사람이 장애를 직접 풀 수 있다.

    부서 호출 실패 등으로 롤백까지 가지 못한 경우는 기다리지 않는다(예전처럼 바로 건너뜀).
    """
    return inc.steps["rollback"].status in ("transfer_queued", "rejected_by_human")


def _recovery_gate(inc: Incident) -> tuple[str, str, str] | None:
    """되돌리기·과거 처리 방식 다시 하기가 끝났거나 사람이 장애를 해제했을 때만 복구를 검증한다.

    원인을 못 찾아 사람에게 넘긴 장애는 사람이 해제하길 기다린다(`wait_for_release`, #909).
    """
    if inc.steps["rollback"].status == "completed" or _past_fix_done(inc):
        return None
    if _human_released(inc):
        return None
    return ("skipped", "no_fix_applied", "선행 단계 미완료(rollback)")


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


def _late_compensation(inc: Incident) -> dict[str, Any]:
    """복구 뒤 다시 센 영향 분석에서 새로 잡힌 고객만 보상 "대기"에 올린다(쿠폰은 사람 승인 뒤)."""
    return {
        "buyer_ids": list(_out(inc, "impact").get("added_customer_ids") or []),
        "reason_code": "payment_failure",
        "impact_ref": f"{inc.context_id}:impact",
    }


def _late_compensation_gate(inc: Incident) -> tuple[str, str, str] | None:
    if _out(inc, "impact").get("added_customer_ids"):
        return None
    return ("skipped", "no_added_customers", "새로 잡힌 영향 고객이 없어 보상 대기 요청 없음")


def _merge_late_compensation(outputs: list[dict[str, Any]]) -> dict[str, Any]:
    """50명씩 나눠 부른 결과를 합친다. 올라간 수는 고객지원이 준 값을 더하기만 한다."""
    merged = {**outputs[0]}
    merged["queued"] = sum(o.get("queued", 0) for o in outputs)
    merged["results"] = [r for o in outputs for r in o.get("results", [])]
    merged["limit_reached"] = any(o.get("limit_reached") for o in outputs)
    merged["notes"] = [n for o in outputs for n in o.get("notes", [])]
    return merged


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


def _finance_record(inc: Incident) -> dict[str, Any]:
    """재무 장부 기록 입력. 구간은 영향 분석과 같고, 영향 숫자는 데이터 결과를 그대로 옮긴다."""
    impact = _out(inc, "impact")
    start, end = inc.impact_window()
    body: dict[str, Any] = {
        "component": inc.component,
        "window_from": iso(start),
        "window_to": iso(end),
        "affected_customer_ids": list(impact.get("affected_customer_ids") or []),
    }
    for key in ("failed_order_count", "affected_customer_count", "damage_amount"):
        if isinstance(impact.get(key), int):
            body[key] = impact[key]
    return body


def _kpi(inc: Incident) -> dict[str, Any]:
    return {"context_ids": [inc.context_id]}


def _similar(inc: Incident) -> dict[str, Any]:
    # 이벤트가 열린 시점에 아는 것(제목)만으로 찾는다. 해결 내용이 있는 사례를 우선 보여 준다.
    return {"query": inc.title, "event_type": inc.event_type, "limit": 3}


# 쇼핑몰 복구 신호(`shop.recovered`)가 알려 준 "어떻게 풀렸는지" → 조치 문장. 사실만 옮긴다
RECOVERY_ACTION = {
    ("fault_released", "admin"): "사람(쇼핑몰 운영자)이 결제 장애 상태를 해제해 정상화",
    ("fault_released", "infra"): "인프라 AI가 결제 장애 상태를 해제해 정상화",
    ("metrics_normal", None): "따로 조치한 기록 없이 결제 지표가 정상으로 돌아옴",
}


def recovery_action(inc: Incident) -> str | None:
    """사람에게 넘긴 뒤 풀린 장애의 조치. 복구 신호에 풀린 방법이 없으면 None(지어내지 않는다)."""
    how = inc.shared.get("recovery")
    if not isinstance(how, dict):
        return None
    recovery, by = how.get("recovery"), how.get("released_by")
    if recovery == "fault_released" and by not in ("admin", "infra"):
        return (
            "결제 장애 상태를 해제해 정상화"
            if by is None
            else f"{by}이(가) 결제 장애 상태를 해제해 정상화"
        )
    return RECOVERY_ACTION.get((recovery, by if recovery == "fault_released" else None))


def _resolution(inc: Incident) -> dict[str, Any]:
    """이번 이벤트의 해결 내용. 단계 결과에 있는 사실만 옮기고 없으면 비운다.

    자동 복구 확인이 끝나지 않았어도(사람에게 넘김) 쇼핑몰 복구 신호를 받았으면 해결로 남기고,
    조치는 복구 신호가 알려 준 풀린 방법, 시간은 열린 시각부터 복구 신호까지다.
    """
    auto = inc.steps["recovery"].status == "completed"
    recovered = auto or inc.recovered_at is not None
    rollback = inc.steps["rollback"]
    action = None
    action_code = None
    if _past_fix_done(inc):
        action = "과거 같은 장애의 처리 방식대로 인프라 AI가 결제 장애 상태를 해제해 정상화"
        action_code = "release_fault"
    elif rollback.status == "completed":
        action_code = "rollback_deployment"
        out = _out(inc, "rollback")
        service = plain_terms(str(out.get("service") or "배포"))
        before, after = out.get("from_version"), out.get("to_version")
        action = f"{service} 이전 버전으로 되돌림" + (
            f" ({before} → {after})" if before and after else ""
        )
    elif recovered:
        # 사람에게 넘긴 뒤 풀림: 사람이 해제하고 복구 검증까지 끝났거나(auto), 복구 신호만 받음.
        # 해제로 풀렸으면 release_fault (다음 같은 장애에서 과거 처리 재사용의 근거)
        action = recovery_action(inc)
        how = inc.shared.get("recovery") or {}
        if isinstance(how, dict) and how.get("recovery") == "fault_released":
            action_code = "release_fault"
    found = _out(inc, "root_cause")
    top = next((h for h in found.get("hypotheses") or [] if h.get("confidence") == "high"), None)
    end = now() if auto or inc.recovered_at is None else inc.recovered_at
    minutes = max(int((end - inc.opened_at).total_seconds() // 60), 0)
    return {
        "context_id": inc.context_id,
        "outcome": "resolved" if recovered else "unresolved",
        "root_cause": kst_text(str(top["explanation"])) if top and top.get("explanation") else None,
        "action": action,
        "minutes_to_resolve": minutes if recovered else None,
        "action_code": action_code if recovered else None,
    }


# 단계별 약속 시간(초): 그 단계가 보통 걸리는 시간. 인사 에이전트 평가가 실제 시간과 비교하고,
# 총괄은 약속의 2배(기본 마감 30초 이상, 최대 300초)를 호출 마감으로 쓴다.
# 임시값이다: 로컬 이벤트 기록(29건)에서 단계별로 걸린 시간의 최댓값에 여유를 둔 것이고
# 운영 기준이 아니다. 단계를 만들 때 약속을 적는 방식으로 바뀌면 이 표는 없어진다.
# 약속이 없는 단계(복구 검증처럼 따로 마감을 주는 단계, 평가에서 빠지는 단계)는 기본 30초다.
# - 10초: 조회·기록·공지(로컬 기록에서 최대 7.5초)
# - 30초: 분석(LLM)·보상·되돌리기·KPI(최대 12~18초, 되돌리기 50초 1건)
# - 60초: 영향 분석(30초 마감에 끊겨 다시 시도된 건이 많다. 한 번에 끝난 건은 5~8초)
PROMISED_SECONDS: dict[str, float] = {
    "diagnose": 10.0,
    "similar_cases": 10.0,
    "impact": 60.0,
    "root_cause": 30.0,
    "rollback": 30.0,
    "related_prs": 10.0,
    "link_incident": 10.0,
    "notice": 10.0,
    "publish_notice": 10.0,
    "compensation": 30.0,
    "recovered_draft": 10.0,
    "recovered_notice": 10.0,
    "finance_record": 10.0,
    "kpi": 30.0,
    "record": 10.0,
    "resolution": 10.0,
    "late_compensation": 30.0,
}


def with_promises(plan: Plan) -> Plan:
    """단계에 약속 시간을 붙인다. 표에 없는 단계는 약속 없이 둔다."""
    for group in (plan.steps, plan.late_steps):
        group[:] = [replace(s, expected_seconds=PROMISED_SECONDS.get(s.id)) for s in group]
    return plan


def scenario_a() -> Plan:
    return with_promises(_scenario_a())


def _scenario_a() -> Plan:
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
            Step(
                "past_fix",
                "release_fault",
                "과거 같은 장애의 처리 방식 다시 하기 (infra)",
                after=("diagnose", "similar_cases"),
                build=_past_fix,
                gate=_past_fix_gate,
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
                "문제 배포를 이전 버전으로 되돌리기 (B)",
                after=("diagnose", "impact"),
                # 사람에게 넘길 때 같은 유형 과거 장애의 조치를 붙이려면 찾기가 먼저 끝나야 한다.
                # 과거 처리 방식으로 이미 풀었으면 되돌리지 않는다(둘 다 기다리기만 한다)
                also_after=("similar_cases", "past_fix"),
                build=_rollback,
                gate=_rollback_gate,
                # 풀렸는지는 복구 검증(필수)이 정한다. 과거 처리 방식으로 풀면 되돌리기는 없다
                required=False,
            ),
            Step(
                "related_prs",
                "find_related_prs",
                "관련 코드 변경 찾기 (dev)",
                after=("diagnose", "impact"),
                build=_related_prs,
                required=False,
            ),
            Step(
                "link_incident",
                "link_incident",
                "이벤트에 코드 변경 연결 (dev)",
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
                also_after=("rollback", "past_fix"),
                build=_recovery,
                gate=_recovery_gate,
                verdict=_recovery_verdict,
                timeout=RECOVERY_TIMEOUT_SECONDS,
                wait_for_release=_handed_to_person,
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
                "end_notice",
                "end_incident_notice",
                "장애 공지 배너 내리기 (marketing)",
                # 정상화 공지가 나갔거나 건너뛴 뒤에 판단한다. 정상화 공지 없이 닫혀도 복구 신호를
                # 받았다면 장애 배너가 남지 않게 한다. 실패해도 이벤트 결과는 그대로다
                also_after=("publish_notice", "recovered_notice"),
                build=_end_notice,
                gate=_end_notice_gate,
                required=False,
                rerun_on_recovery=True,
                # 판매 서버가 잠깐 멈춰 세 번 다 실패해도 배너가 남지 않게 나중에 다시 시도한다
                late_retry=True,
            ),
            Step(
                "finance_record",
                "record_incident_finance",
                "장애 비용 장부 기록 (finance)",
                # 보상이 끝나야 쿠폰 합계가 맞고, 복구 확인 뒤라야 구간이 닫힌다. 실패해도 이어 간다
                also_after=("compensation", "recovery"),
                build=_finance_record,
                required=False,
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
                    "end_notice",
                    "link_incident",
                    "compensation",
                    "finance_record",
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
        late_steps=[
            Step(
                "late_compensation",
                "propose_incident_compensation",
                "복구 뒤 놓친 고객 보상 대기 (support)",
                build=_late_compensation,
                gate=_late_compensation_gate,
                split=("buyer_ids", COMPENSATION_BATCH),
                merge=_merge_late_compensation,
                required=False,
            ),
        ],
    )
