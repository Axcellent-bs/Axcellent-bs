"""개발 작업 결과 (`list_dev_outcomes`, read): 인사·총괄이 부서·에이전트 평가 근거로 읽는다.

- 숫자는 개발 서비스가 이미 남긴 기록 그대로다 (작업 `CodingRun`, PR `PR`). 새로 추정하지 않는다.
- 복잡도는 코드 규칙이다: PR이 없으면 unknown, 바뀐 줄 ≤ SIMPLE_LINES · 파일 ≤ SIMPLE_FILES ·
  리뷰 불일치 0이면 simple, 아니면 complex. LLM이 정하지 않는다.
- 토큰은 서버 AI(회사 키)만 센다. 내 Claude(구독형)는 개발자 개인 구독이라 0이다.
"""

from __future__ import annotations

from datetime import datetime

from ax_contracts.schemas.planning import DevOutcome, DevOutcomeSummary
from ax_workflow.models import PR, State, Task

SIMPLE_LINES = 50
SIMPLE_FILES = 3


def _pr_of(s: State, task_id: str) -> PR | None:
    """작업의 PR: 머지된 것 → 열린 것 → 닫힌 것 순서로 마지막 것."""
    prs = [p for p in s.prs if p.task == task_id]
    for pick in (lambda p: p.merged, lambda p: not p.closed, lambda p: True):
        hit = [p for p in prs if pick(p)]
        if hit:
            return hit[-1]
    return None


def outcome_of(s: State, t: Task) -> DevOutcome | None:
    cr = t.coding
    if cr is None:
        return None
    pr = _pr_of(s, t.id)
    mismatches = pr.reviews.count("불일치") if pr else max(0, cr.attempts - 1)
    lines = pr.gate.lines if pr else None
    files = pr.gate.files if pr else None
    if pr is None:
        complexity = "unknown"
    elif (
        lines is not None
        and files is not None
        and lines <= SIMPLE_LINES
        and files <= SIMPLE_FILES
        and not mismatches
    ):
        complexity = "simple"
    else:
        complexity = "complex"
    # 사람 확인을 거쳤나: 관문이 PR 열기를 보류했거나, 자동 머지가 멈췄거나, 사람이 머지했거나
    held = bool(cr.hold_key) or (
        pr is not None and (pr.auto_review is not None or (pr.merged and not pr.auto))
    )
    sub = cr.mode == "subscription"
    return DevOutcome(
        task_id=t.id,
        spec_id=t.spec,
        title=t.title,
        mode=cr.mode,
        coder=cr.claimed_by if sub else cr.model,
        status=cr.status,
        complexity=complexity,  # type: ignore[arg-type]
        changed_lines=lines,
        changed_files=files,
        attempts=cr.attempts,
        llm_calls=0 if sub else cr.llm_calls,
        llm_tokens=0 if sub else cr.llm_tokens,
        review_mismatches=mismatches,
        held=held,
        pr_number=(pr.vcs_number or pr.number) if pr else None,
        merged=bool(pr and pr.merged) or cr.status == "merged",
        merged_at=pr.merged_at if pr else None,
        updated_at=cr.updated_at,
    )


def _ratio(n: float, d: int) -> float | None:
    return round(n / d, 3) if d else None


def summarize(rows: list[DevOutcome]) -> DevOutcomeSummary:
    merged = [r for r in rows if r.merged]
    agent_merged = [r for r in merged if r.mode == "agent"]
    tokens = sum(r.llm_tokens for r in rows)
    by_mode: dict[str, int] = {}
    by_complexity: dict[str, int] = {}
    for r in rows:
        by_mode[r.mode] = by_mode.get(r.mode, 0) + 1
        by_complexity[r.complexity] = by_complexity.get(r.complexity, 0) + 1
    written = [r for r in rows if r.attempts > 0]
    return DevOutcomeSummary(
        total=len(rows),
        merged=len(merged),
        failed=sum(1 for r in rows if r.status == "failed"),
        covered=sum(1 for r in rows if r.status == "covered"),
        by_mode=by_mode,
        by_complexity=by_complexity,
        llm_tokens=tokens,
        tokens_per_merged=_ratio(sum(r.llm_tokens for r in agent_merged), len(agent_merged)),
        avg_attempts=_ratio(sum(r.attempts for r in written), len(written)),
        first_pass_rate=_ratio(sum(1 for r in merged if not r.review_mismatches), len(merged)),
        held_rate=_ratio(sum(1 for r in rows if r.held), len(rows)),
    )


def list_outcomes(
    s: State,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    coder: str | None = None,
) -> tuple[list[DevOutcome], DevOutcomeSummary]:
    rows: list[DevOutcome] = []
    for t in s.tasks:
        o = outcome_of(s, t)
        if o is None:
            continue
        at = o.updated_at
        if since is not None and (at is None or at < since):
            continue
        if until is not None and (at is None or at > until):
            continue
        if coder == "agent" and o.mode != "agent":
            continue
        if coder not in (None, "agent") and (o.mode != "subscription" or o.coder != coder):
            continue
        rows.append(o)
    return rows, summarize(rows)
