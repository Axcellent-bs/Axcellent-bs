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

from collections import Counter
from typing import Any

from ax_orchestrator.models import Incident

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
        add(
            "evaluation",
            cid,
            f"{title}: 일한 시간 {_seconds(ev.get('work_seconds'))}, 시도 {ev.get('attempts', 0)}회"
            f"{', ' + ev['reason'] if ev.get('reason') else ''}",
            {
                "verdict": ev.get("verdict"),
                "work_seconds": ev.get("work_seconds"),
                "attempts": ev.get("attempts", 0),
                "limit_seconds": ev.get("limit_seconds"),
            },
        )
        inc = incidents.get(cid)
        if inc is not None:
            found = True
            run = inc.steps.get(step_id)
            facts: dict[str, Any] = {"event_type": inc.event_type, "component": inc.component}
            parts = [f"이벤트 유형 {inc.event_type or '알 수 없음'}"]
            if run is not None:
                facts.update(
                    status=run.status,
                    attempts=run.attempts,
                    reason_code=run.reason_code,
                    note=(run.note or "")[:NOTE_MAX] or None,
                )
                parts.append(f"단계 상태 {run.status}, 시도 {run.attempts}회")
                if run.reason_code:
                    parts.append(f"사유 코드 {run.reason_code}")
                if run.note:
                    parts.append(f"메모 {run.note[:NOTE_MAX]}")
            add("orchestrator", cid, f"{title}: " + ", ".join(parts), facts)
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
            parts = [f"이벤트 유형 {facts['event_type'] or '알 수 없음'}"]
            if res:
                parts.append(f"결과 {res.get('outcome')}")
                if facts["action_code"]:
                    parts.append(f"처리 방식 {facts['action_code']}")
                if facts["action"]:
                    parts.append(f"조치 {facts['action']}")
            add("card", cid, f"{title}: 아카이브 카드 " + ", ".join(parts), facts)
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
        "event_type": "같은 유형의 이벤트({v})에서 반복됐어요",
        "reason_code": "단계가 같은 사유 코드({v})로 끝났어요",
        "action_code": "같은 처리 방식({v})으로 끝난 이슈가 반복됐어요",
    }
    for key in CAUSAL:
        for value, group in _group(items, key).items():
            contexts = sorted({g["context_id"] for g in group})
            if len(contexts) >= 2:
                found.append(
                    {
                        "kind": key,
                        "value": value,
                        "text": labels[key].format(v=value),
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
                "text": f"근거 이슈 {len(evidence)}건 중 {len(retried)}건에서 다시 시도가 있었어요",
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
                "text": (
                    f"{len(slow)}건이 기준 시간({_seconds(limit)})을 넘었고 "
                    f"평균 {_seconds(avg)} 걸렸어요"
                ),
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
        "확인 필요였어요."
    )
    notes: list[str] = []
    if cards_note:
        notes.append(cards_note)
    if missing:
        notes.append(f"총괄에도 카드에도 기록이 없어 근거에서 빠진 이슈 {len(missing)}건")
    if causal:
        status = "diagnosed"
        body = " ".join(f"{c['text']} ({', '.join(c['evidence_ids'])})." for c in causal)
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
                "원인을 가릴 만큼 겹치는 근거(같은 이벤트 유형·사유 코드·처리 방식)가 없어 "
                "원인은 말하지 않아요.",
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
