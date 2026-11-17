"""에이전트 평가 진단 1차: 근거를 모으고 공통점을 규칙으로 찾는다 (LLM 없음).

인사가 "이 에이전트의 이 단계가 최근 N건 중 M건 확인 필요"라고 보내면(`diagnose_agent_issue`) 총괄은

1. 근거를 모은다: 인사가 보낸 평가 근거, 총괄이 그 이슈에서 기록한 단계(상태·시도·사유 코드),
   아카이브 이슈 카드(이벤트 유형·처리 방식·해결 기록). 근거마다 번호(`E1`, `E2`…)를 붙인다.
2. 이슈들에서 겹치는 점을 센다. 같은 이벤트 유형, 같은 사유 코드, 같은 처리 방식, 다시 시도,
   기준 시간 초과.
3. 규칙 문장으로 진단한다. **원인을 가릴 만한 겹침**(이벤트 유형·사유 코드·처리 방식)이
   둘 이상의 이슈에 있으면 `diagnosed`, 시간·재시도 패턴뿐이면 `insufficient`
   (근거 부족, 에이전트에 전달하지 않는다).

이 진단은 평가가 아니라 원인 설명이다. 규칙은 "무엇이 겹치는지"까지만 말하고 "왜"는 지어내지 않는다.
원인 추정과 권장 행동은 LLM 요약 단계(선택)에서 근거 번호를 인용해 만든다.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from ax_orchestrator.models import Incident
from ax_orchestrator.naming import COMPONENT_KO, NOTE_KO, plain_terms

AGENT_KO = {
    "infra": "인프라 AI",
    "data": "데이터분석 AI",
    "dev": "개발 AI",
    "support": "고객지원 AI",
    "marketing": "마케팅 AI",
    "finance": "재무 AI",
    "archive": "아카이브 AI",
    "hr": "인사 AI",
}

# 원인을 가릴 만한 겹침(근거 이슈 둘 이상에서 같아야 한다). 시간·재시도는 확인 필요의 모양일 뿐이다
CAUSAL = ("event_type", "reason_code", "action_code")
NOTE_MAX = 80

# 사람이 읽는 말로 바꾸는 이름표. 대응표에 없는 코드는 영어 그대로 보이지 않고 그 부분을 뺀다
METRIC_SHORT_KO = {
    "error_rate_5xx": "서버 오류",
    "p95_latency_ms": "응답 지연",
    "order_fail_rate": "주문 실패",
    "payment_fail_rate": "결제 실패",
}
ACTION_KO = {
    "release_fault": "장애 상태 해제",
    "rollback_deployment": "이전 버전으로 되돌리기",
}
REASON_KO = {
    "TIMEOUT": "응답 마감을 넘김",
    "LOOP_DETECTED": "같은 요청이 돌고 돌아 중단됨",
    "HOP_LIMIT": "여러 부서를 거치다 중단됨",
    "dept_input": "다른 부서의 판단이 필요함",
    "customer_input": "고객 자료가 필요함",
    "restart_interrupted": "서버가 다시 켜져 중단됨",
    "review_timeout": "승인 기다리다 시간이 지남",
}
STEP_STATUS_KO = {
    "completed": "끝남",
    "running": "진행 중",
    "skipped": "건너뜀",
    "held": "보류됨",
    "rejected_by_human": "사람이 거절함",
    "transfer_queued": "사람에게 넘김",
    "waiting_async": "결과를 기다리는 중",
    "waiting_human": "사람 확인을 기다리는 중",
    "failed": "실패함",
}
NOTE_EXTRA_KO = {"undetermined": "원인 미확정", "suspected": "원인 의심 있음"}
OUTCOME_KO = {"resolved": "해결됨", "unresolved": "해결 안 됨", "mitigated": "완화됨"}
_ENGLISH = re.compile(r"[A-Za-z]{3,}")


def event_type_ko(value: str | None) -> str | None:
    """`payment:error_rate_5xx` → `결제 서버 오류`. 대응표에 없으면 None."""
    if not value or ":" not in value:
        return None
    component, metric = value.split(":", 1)
    if component in COMPONENT_KO and metric in METRIC_SHORT_KO:
        return f"{COMPONENT_KO[component]} {METRIC_SHORT_KO[metric]}"
    return None


def _plain_text(text: str | None) -> str | None:
    """한글로 쓰인 글만 남긴다. 영어 코드가 남으면 쉬운 말로 바꿔 보고, 그래도 남으면 뺀다."""
    if not text:
        return None
    text = plain_terms(text)
    return None if _ENGLISH.search(text) else text


def _paren(value: str | None) -> str:
    return f"({value})" if value else ""


def _line(title: str, source: str, parts: list[str]) -> str:
    return f"{title}: {source}" + "".join(f" · {p}" for p in parts if p)


def agent_ko(agent: str) -> str:
    return AGENT_KO.get(agent, agent)


def _seconds(value: float | None) -> str:
    return f"{value:.0f}초" if value is not None else "알 수 없음"


def collect_items(
    evidence: list[dict[str, Any]],
    step_id: str,
    incidents: dict[str, Incident],
    cards: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """근거 목록과, 근거를 못 찾은 이슈 번호들. 근거마다 `id`(E1…)를 붙인다.

    `cards`는 이슈 번호 → 아카이브 이슈 카드(`Card` 딕셔너리). 못 읽었으면 비어 총괄 기록만 쓴다.
    """
    items: list[dict[str, Any]] = []
    missing: list[str] = []

    def add(kind: str, context_id: str, text: str, facts: dict[str, Any]) -> None:
        items.append(
            {
                "id": f"E{len(items) + 1}",
                "kind": kind,
                "context_id": context_id,
                "text": text,
                "facts": facts,
            }
        )

    for ev in evidence:
        cid = ev["context_id"]
        found = False
        title = ev.get("title") or cid
        limit = ev.get("limit_seconds")
        add(
            "evaluation",
            cid,
            f"{title}: {_seconds(ev.get('work_seconds'))} 걸렸고"
            f"{f'(기준 {_seconds(limit)})' if limit is not None else ''} "
            f"{ev.get('attempts', 0)}번 시도했어요",
            {
                "verdict": ev.get("verdict"),
                "work_seconds": ev.get("work_seconds"),
                "attempts": ev.get("attempts", 0),
                "limit_seconds": limit,
            },
        )
        inc = incidents.get(cid)
        if inc is not None:
            found = True
            run = inc.steps.get(step_id)
            facts: dict[str, Any] = {"event_type": inc.event_type, "component": inc.component}
            parts = [p for p in (event_type_ko(inc.event_type),) if p]
            if run is not None:
                facts.update(
                    status=run.status,
                    attempts=run.attempts,
                    reason_code=run.reason_code,
                    note=(run.note or "")[:NOTE_MAX] or None,
                )
                parts.append(STEP_STATUS_KO.get(run.status, ""))
                parts.append(f"{run.attempts}번 시도")
                parts.append(REASON_KO.get(run.reason_code or "", ""))
                note = (run.note or "")[:NOTE_MAX]
                parts.append(
                    NOTE_EXTRA_KO.get(note) or NOTE_KO.get(note) or _plain_text(note) or ""
                )
            add("orchestrator", cid, _line(title, "총괄 기록", parts), facts)
        card = cards.get(cid)
        if card is not None:
            found = True
            extra = card.get("extra") or {}
            res = extra.get("resolution") or {}
            facts = {
                "event_type": extra.get("event_type"),
                "outcome": res.get("outcome"),
                "action_code": res.get("action_code"),
                "action": (res.get("action") or "")[:NOTE_MAX] or None,
            }
            parts = [event_type_ko(facts["event_type"]) or ""]
            if res:
                parts.append(OUTCOME_KO.get(str(res.get("outcome")), ""))
                parts.append(
                    ACTION_KO.get(facts["action_code"] or "") or _plain_text(facts["action"]) or ""
                )
            add("card", cid, _line(title, "아카이브 기록", parts), facts)
        if not found:
            missing.append(cid)
    return items, missing


def _group(items: list[dict[str, Any]], key: str) -> dict[Any, list[dict[str, Any]]]:
    """값(없으면 건너뜀)별로 근거를 묶는다. 같은 이슈에서 나온 근거는 한 이슈로 센다."""
    groups: dict[Any, list[dict[str, Any]]] = {}
    for item in items:
        value = item["facts"].get(key)
        if value:
            groups.setdefault(value, []).append(item)
    return groups


def find_commonalities(
    items: list[dict[str, Any]], evidence: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """근거 이슈 둘 이상에서 겹치는 점. 항목마다 kind·text·causal·evidence_ids·contexts가 있다."""
    found: list[dict[str, Any]] = []
    labels = {
        "event_type": lambda v: f"같은 종류의 이벤트{_paren(event_type_ko(v))}에서 반복됐어요",
        "reason_code": lambda v: f"같은 이유{_paren(REASON_KO.get(v))}로 끝났어요",
        "action_code": lambda v: (
            f"같은 방식{_paren(ACTION_KO.get(v))}으로 해결된 이슈가 반복됐어요"
        ),
    }
    for key in CAUSAL:
        for value, group in _group(items, key).items():
            contexts = sorted({g["context_id"] for g in group})
            if len(contexts) >= 2:
                found.append(
                    {
                        "kind": key,
                        "value": value,
                        "text": labels[key](value),
                        "causal": True,
                        "evidence_ids": [g["id"] for g in group],
                        "contexts": contexts,
                    }
                )
    # 시간·재시도: 확인 필요가 된 모양(원인이 아니다)
    retried = [e for e in evidence if (e.get("attempts") or 0) > 1]
    if len(retried) >= 2:
        ids = [i["id"] for i in items if i["kind"] == "evaluation" and i["facts"]["attempts"] > 1]
        found.append(
            {
                "kind": "retry",
                "value": len(retried),
                "text": (
                    f"{len(retried)}건 모두 다시 시도했어요"
                    if len(retried) == len(evidence)
                    else f"{len(evidence)}건 중 {len(retried)}건에서 다시 시도했어요"
                ),
                "causal": False,
                "evidence_ids": ids,
                "contexts": sorted({e["context_id"] for e in retried}),
            }
        )
    slow = [
        e
        for e in evidence
        if e.get("work_seconds") is not None
        and e.get("limit_seconds") is not None
        and e["work_seconds"] > e["limit_seconds"]
    ]
    if len(slow) >= 2:
        limit = slow[0]["limit_seconds"]
        avg = sum(e["work_seconds"] for e in slow) / len(slow)
        ids = [
            i["id"]
            for i in items
            if i["kind"] == "evaluation"
            and (i["facts"].get("work_seconds") or 0) > (i["facts"].get("limit_seconds") or 1e9)
        ]
        found.append(
            {
                "kind": "slow",
                "value": round(avg, 1),
                "text": f"평균 {_seconds(avg)} 걸렸어요(기준 {_seconds(limit)})",
                "causal": False,
                "evidence_ids": ids,
                "contexts": sorted({e["context_id"] for e in slow}),
            }
        )
    return found


def diagnose(
    row: dict[str, Any],
    incidents: dict[str, Incident],
    cards: dict[str, dict[str, Any]],
    *,
    cards_note: str | None = None,
) -> dict[str, Any]:
    """진단 결과 하나. `status`는 diagnosed(원인을 가릴 겹침 있음)나 insufficient(근거 부족)."""
    evidence = row["evidence"]
    items, missing = collect_items(evidence, row["step_id"], incidents, cards)
    commons = find_commonalities(items, evidence)
    causal = [c for c in commons if c["causal"]]
    who = agent_ko(row["agent"])
    head = (
        f"{who}의 '{row['step_label']}' 단계가 최근 {row['window']}건 중 {row['flagged']}건에서 "
        "확인이 필요했어요."
    )
    notes: list[str] = []
    if cards_note:
        notes.append(cards_note)
    if missing:
        notes.append(f"총괄에도 카드에도 기록이 없어 근거에서 빠진 이슈 {len(missing)}건")
    if causal:
        status = "diagnosed"
        body = " ".join(f"{c['text']}." for c in causal)
        shape = " ".join(f"{c['text']}." for c in commons if not c["causal"])
        summary = " ".join(part for part in (head, body, shape) if part)
    else:
        status = "insufficient"
        shape = " ".join(f"{c['text']}." for c in commons if not c["causal"])
        summary = " ".join(
            part
            for part in (
                head,
                shape,
                "겹치는 이벤트 종류·이유·해결 방식이 없어서 원인은 말하지 않아요.",
            )
            if part
        )
    return {
        "status": status,
        "method": "rules",
        "summary": summary,
        "findings": commons,
        "items": items,
        "notes": notes,
        "causal_count": len(causal),
        "counts": dict(Counter(i["kind"] for i in items)),
    }
