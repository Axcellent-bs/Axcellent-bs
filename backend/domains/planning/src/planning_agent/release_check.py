"""배포 전 기획서 대조 (`request_release_check`, 2026-11-02).

개발이 "완료"라고 하면 배포하기 전에 기획이 결과가 기획서와 맞는지 본다. 판정은 코드 규칙이다
(LLM을 부르지 않는다). 계약: `ax_contracts.schemas.planning` 배포 전 대조 절.

연결 규칙 (하나라도 걸리면 `hold`)
- 기획서가 기획 쪽에 있다 (`spec_unknown`) · 확정돼 있다 (`spec_not_confirmed`)
- 답 안 된 질문이 없다 (`open_question`)
- 요구사항 문단마다 작업이 있다 (`para_without_task`)
- 작업마다 머지된 PR이 있다 (`task_not_merged`)
- 기획서 작업에 묶이지 않은 PR이 없다 (`pr_outside_spec`)
- 문단에 열린 장애 표시가 없다 (`linked_incident_open`)

내용 대조 (연결은 통과했을 때)
- 개발이 PR마다 이미 한 검수(`ReleasePr.review`)를 문단별로 모은다. 모든 문단이 `match`일 때만
  `ready`, 하나라도 `mismatch`·`none`이면 `needs_human`. 검수 문장(`review_note`)은 근거로만 싣는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ax_contracts.schemas.planning import ReleaseCheckItem, RequestReleaseCheckInput
from ax_workflow.models import State

NOTE_MAX = 200


@dataclass
class ReleaseJudgement:
    verdict: str  # ready · hold · needs_human
    items: list[ReleaseCheckItem] = field(default_factory=list)
    hold_reasons: list[str] = field(default_factory=list)


def _add(reasons: list[str], code: str) -> None:
    if code not in reasons:
        reasons.append(code)


def judge(state: State, inp: RequestReleaseCheckInput) -> ReleaseJudgement:
    """기획 상태와 개발이 보낸 작업·PR을 대조한다. 상태는 바꾸지 않는다."""
    sp = next((x for x in state.specs if x.id == inp.spec_id), None)
    if sp is None:
        return ReleaseJudgement("hold", hold_reasons=["spec_unknown"])
    reasons: list[str] = []
    req = next((r for r in state.requests if r.id == inp.request_id), None)
    if req is not None and req.paused:
        _add(reasons, "paused_by_human")
    if sp.status != "확정":
        _add(reasons, "spec_not_confirmed")
    if sp.question:
        _add(reasons, "open_question")

    tasks = [t for t in state.tasks if t.spec == sp.id]
    task_ids = {t.id for t in tasks}
    for pr in inp.prs:
        if pr.task_id is None or pr.task_id not in task_ids:
            _add(reasons, "pr_outside_spec")

    items: list[ReleaseCheckItem] = []
    for para in (p for p in sp.paras if p.kind == "req"):
        para_tasks = [t.id for t in tasks if t.para == para.id]
        prs = [p for p in inp.prs if p.task_id in para_tasks]
        merged = [p for p in prs if p.merged]
        if para.incident:
            status = "incident_open"
            _add(reasons, "linked_incident_open")
        elif not para_tasks:
            status = "missing_task"
            _add(reasons, "para_without_task")
        elif any(not any(p.merged and p.task_id == t for p in prs) for t in para_tasks):
            status = "not_merged"
            _add(reasons, "task_not_merged")
        else:
            status = "covered"
        reviews = [p.review for p in merged] or ["none"]
        if "mismatch" in reviews:
            content = "mismatch"
        elif all(r == "match" for r in reviews):
            content = "match"
        else:
            content = "none"
        notes = [f"PR #{p.number}: {p.review_note}"[:NOTE_MAX] for p in merged if p.review_note]
        items.append(
            ReleaseCheckItem(
                para_id=para.id,
                status=status,  # type: ignore[arg-type]
                task_ids=para_tasks,
                pr_numbers=[p.number for p in prs],
                content=content,  # type: ignore[arg-type]
                content_notes=notes,
            )
        )

    if reasons:
        return ReleaseJudgement("hold", items, reasons)
    if all(i.content == "match" for i in items) and items:
        return ReleaseJudgement("ready", items)
    return ReleaseJudgement("needs_human", items)
