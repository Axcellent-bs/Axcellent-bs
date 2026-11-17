"""같은 단계의 확인 필요가 반복되는 에이전트를 찾는다 (시간·판정 계산만, LLM 없음).

저장된 판정(`EvaluatedEvent`)을 새로운 이슈부터 훑어, 같은 (에이전트, 단계)의 최근 기록 중
확인 필요가 조건(`criteria.toml [repeat]`)만큼 쌓였으면 총괄에 진단을 요청할 거리로 돌려준다.
원인은 모른다. 어느 에이전트의 어느 단계가 몇 번 그랬는지와 근거 이슈까지만 만든다.
"""

from __future__ import annotations

from dataclasses import dataclass

from ax_contracts.schemas import EvaluatedEvent, EvaluatedStep
from hr_agent.dept_eval.criteria import RepeatRule

# 판정할 수 있었던 기록. 사람에게 넘김·기록 없음·진행 중은 세지 않는다(탓이 아니거나 아직 모름)
COUNTABLE = {"normal", "check", "silent", "blocked", "approval_expired"}


@dataclass(frozen=True)
class Finding:
    agent: str
    step_id: str
    step_label: str
    window: int
    flagged: int
    evidence: list[tuple[EvaluatedEvent, EvaluatedStep]]  # 새로운 이슈부터


def find_repeats(
    events: list[EvaluatedEvent], rule: RepeatRule, *, context_id: str | None = None
) -> list[Finding]:
    """`events`는 새로운 이슈부터. `context_id`를 주면 그 이슈가 최근 기록에 든 단계만 돌려준다.

    방금 판정이 저장된 이슈를 주면, 그 이슈와 무관한 옛 반복을 이슈마다 다시 보내지 않는다.
    """
    history: dict[tuple[str, str], list[tuple[EvaluatedEvent, EvaluatedStep]]] = {}
    for event in events:
        for step in event.steps:
            if step.verdict in COUNTABLE:
                history.setdefault((step.dept, step.step_id), []).append((event, step))
    found: list[Finding] = []
    for (agent, step_id), rows in history.items():
        if len(rows) < rule.min_records:
            continue
        recent = rows[: rule.window]
        if context_id is not None and all(e.context_id != context_id for e, _ in recent):
            continue
        flagged = [(e, s) for e, s in recent if s.verdict in rule.flagged_verdicts]
        if len(flagged) < rule.min_flagged:
            continue
        found.append(
            Finding(
                agent=agent,
                step_id=step_id,
                step_label=flagged[0][1].label,
                window=len(recent),
                flagged=len(flagged),
                evidence=flagged,
            )
        )
    return sorted(found, key=lambda f: (f.agent, f.step_id))
