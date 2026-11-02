"""구독형 일감 대기열: 개발자 본인 Claude(본인 PC·본인 구독)가 MCP로 코딩 작업을 가져간다.

서버 코딩 에이전트(coder.py, API 키) 대신 개발자의 Claude Code가 코딩하고 PR을 올린다.
코딩 토큰은 개발자 구독에서 나가고, 이 서버는 일감·진행·PR 번호만 기록한다.

흐름 (작업 `coding.mode == "subscription"`만):
1. `create_dev_task(mode="subscription")` → `queued` (서버 에이전트를 띄우지 않는다).
2. `claim` → `claimed`. 가져간 사람·시각과 임대 끝 시각(`lease_until`)을 적는다
   (관문 dev_task_claim A).
3. `progress` → `writing`. 진행 글을 적고 임대를 늘린다 (작은 기록이라 관문 없음, coding_step과
   같다).
4. `report_pr` → 개발자가 올린 PR을 형상관리에서 읽어 기존 PR 기록으로 잇는다.
   크기·경로는 형상관리 값으로 세고 `generate_pr`(B) 관문을 그대로 지난다. 통과면 `opened`,
   보류·거절이면 PR은 기록하되 `waiting_human`(사람 확인). 머지는 사람 머지 버튼(`merge_pr` B)
   이나 형상관리에서 직접 머지 → `sync_prs`가 맞춘다.
5. `release` → 다시 `queued` (관문 dev_task_release A). 임대가 끝나도록 보고가 없으면
   `expire_leases`가 같은 길로 되돌린다 (PR 맞추기 루프와 목록 읽기 때).

- 사람(개발자) 토큰만 받는다. 가져간 사람만 보고·반납할 수 있다.
- PR 제목에 작업 id(예: `T-D3`)가 있어야 잇는다 (다른 PR을 잘못 잇지 않게).
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from ax_workflow.core import ActionError, GateRefused
from planning_agent import coder, vcs, vcs_bridge
from planning_agent.domain.autocheck import WAIT_HUMAN, pr_check
from planning_agent.domain.models import PR, CodingFile, CodingRun, PrGate, State, Task
from planning_agent.vcs.base import BRANCH_PREFIX, VcsError

if TYPE_CHECKING:
    from planning_agent.domain.service import PlanningService

SUBSCRIPTION = "subscription"
HELD = ("claimed", "writing")  # 개발자 Claude가 쥐고 있는 상태 (임대가 걸린다)
LEASE_MINUTES = 30  # PLANNING_LEASE_MINUTES
MAX_PROGRESS_CHARS = 500
COMMIT_RULE = (
    "커밋 메시지와 PR 제목에 작업 id를 넣는다 (예: '{task}: 배너 문구 변경'). "
    "브랜치는 {branch}, 기준 브랜치는 {base}. PR을 올리면 report_pr로 번호를 알린다."
)


def lease_minutes() -> int:
    """진행 보고 없이 쥐고 있을 수 있는 분 (`PLANNING_LEASE_MINUTES`, 기본 30)."""
    try:
        return max(1, int(os.getenv("PLANNING_LEASE_MINUTES", str(LEASE_MINUTES))))
    except ValueError:
        return LEASE_MINUTES


def _error(code: str, message: str) -> Exception:
    return ActionError(code, message)


def _task(s: State, task_id: str) -> tuple[Task, CodingRun]:
    x = next((t for t in s.tasks if t.id == task_id), None)
    if x is None or x.coding is None:
        raise _error("NOT_FOUND", f"코딩 작업 {task_id}가 없다")
    if x.coding.mode != SUBSCRIPTION:
        raise _error("NOT_SUBSCRIPTION", f"{task_id}는 서버 코딩 에이전트 작업이다")
    return x, x.coding


def _mine(cr: CodingRun, who: str) -> None:
    """가져간 사람만 보고·반납한다."""
    if cr.status not in HELD:
        raise _error("NOT_CLAIMED", "가져간 작업이 아니다 (임대가 끝났으면 claim부터 다시)")
    if cr.claimed_by != who:
        raise _error("NOT_CLAIMANT", f"{cr.claimed_by}가 가져간 작업이다")


def _expired(cr: CodingRun, now: datetime) -> bool:
    return cr.status in HELD and (cr.lease_until is None or cr.lease_until <= now)


def _ctx(svc: PlanningService, s: State, x: Task) -> str:
    sp = next((p for p in s.specs if p.id == x.spec), None)
    req = svc._request_of_spec(s, sp) if sp else None
    return req.context_id if req else f"task:{x.id}"


def branch_of(task_id: str) -> str:
    return f"{BRANCH_PREFIX}{task_id.lower()}"


def _title_has(title: str, task_id: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(task_id)}(?!\d)", title, re.IGNORECASE) is not None


def spec_of(s: State, x: Task) -> dict[str, Any]:
    """개발자 Claude에게 주는 작업 명세 (기획·완료 기준·경로 힌트·레포·브랜치·커밋 규칙)."""
    cr = x.coding
    assert cr is not None
    sp = next((p for p in s.specs if p.id == x.spec), None)
    paras = sp.paras if sp else []
    r1 = next((p for p in paras if p.id == "R1"), None)
    conn = vcs.find_connection(cr.connection) if cr.connection else None
    base = conn.base_branch if conn else "dev"
    branch = branch_of(x.id)
    return {
        "task_id": x.id,
        "title": x.title,
        "plan": sp.problem if sp else "",
        "done": [p.text for p in paras if p.kind == "done"],
        "paths": list(r1.paths) if r1 else [],
        "repo": {
            "connection": cr.connection,
            "repo": conn.repo if conn else "",
            "kind": conn.kind if conn else "",
            "base_branch": base,
            "mount": conn.mount if conn else "",
        },
        "branch": branch,
        "pr_title": f"{x.id}: {x.title}",
        "commit_rule": COMMIT_RULE.format(task=x.id, branch=branch, base=base),
        "status": cr.status,
        "claimed_by": cr.claimed_by,
        "lease_until": cr.lease_until.isoformat() if cr.lease_until else None,
        "progress": cr.progress,
        "pr": cr.pr,
    }


def available(svc: PlanningService, who: str) -> list[dict[str, Any]]:
    """가져갈 수 있는 작업(대기 중)과 이 사람이 쥐고 있는 작업."""
    rows = []
    for x in svc.state.tasks:
        cr = x.coding
        if cr is None or cr.mode != SUBSCRIPTION:
            continue
        if cr.status == "queued" or (cr.status in HELD and cr.claimed_by == who):
            row = spec_of(svc.state, x)
            row["mine"] = cr.status in HELD
            rows.append(row)
    return rows


async def claim(svc: PlanningService, task_id: str, who: str) -> dict[str, Any]:
    """대기 중인 작업을 가져간다 (같은 사람이 다시 부르면 임대만 늘린다). 전체 명세를 준다."""
    box: dict[str, Any] = {}

    async def run(s: State) -> None:
        x, cr = _task(s, task_id)
        now = svc.clock()
        again = cr.status in HELD and cr.claimed_by == who
        if cr.status in HELD and not again and not _expired(cr, now):
            raise _error("ALREADY_CLAIMED", f"{task_id}는 {cr.claimed_by}가 가져갔다")
        if cr.status != "queued" and cr.status not in HELD:
            raise _error("NOT_CLAIMABLE", f"{task_id}는 지금 가져갈 수 없다 ({cr.status})")
        mins = lease_minutes()
        if not again:
            ctx = _ctx(svc, s, x)
            took = f" ({cr.claimed_by} 임대 만료)" if cr.status in HELD else ""
            text = f"{task_id} {who} 개발자 Claude가 가져감{took} · 임대 {mins}분"
            n = sum(1 for g in svc.gate_calls if g.action == "dev_task_claim") + 1
            await svc._execute(
                "dev_task_claim", ctx, f"{task_id}:{n}", text,
                {"claimed_by": who, "connection": cr.connection},
            )  # fmt: skip
            s.activity.append(svc._act(now, ctx, "human", "dev", "dev_task_claim", "사람", text))
            cr.status = "claimed"
            cr.claimed_at = now
            cr.progress = None
            cr.error = None
        cr.claimed_by = who
        cr.lease_until = now + timedelta(minutes=mins)
        cr.updated_at = now
        x.owner = who
        box["spec"] = spec_of(s, x)

    await svc._apply(run)
    return box["spec"]


async def progress(svc: PlanningService, task_id: str, who: str, text: str) -> dict[str, Any]:
    """진행 글을 적고 임대를 늘린다. 글이 바뀔 때만 활동 한 줄을 남긴다."""
    note = " ".join(str(text or "").split())[:MAX_PROGRESS_CHARS]
    if not note:
        raise _error("INVALID_INPUT", "진행 글(text)이 비었다")
    box: dict[str, Any] = {}

    async def run(s: State) -> None:
        x, cr = _task(s, task_id)
        _mine(cr, who)
        now = svc.clock()
        if note != cr.progress:
            ctx = _ctx(svc, s, x)
            line = f"{task_id} {who} 진행: {note[:120]}"
            s.activity.append(svc._act(now, ctx, "human", "dev", "work.progress", "API", line))
        cr.status = "writing"
        cr.progress = note
        cr.lease_until = now + timedelta(minutes=lease_minutes())
        cr.updated_at = now
        box["spec"] = spec_of(s, x)

    await svc._apply(run)
    return box["spec"]


async def report_pr(
    svc: PlanningService, task_id: str, who: str, number: int, url: str | None = None
) -> dict[str, Any]:
    """개발자가 올린 PR을 기록에 잇는다. 형상관리에서 제목·브랜치·바뀐 파일을 읽어 확인한다.

    PR 표시 id·관문(`generate_pr` B)·대상 레포는 서버 에이전트 PR(`coding_open`)과 같은 모양이라
    PR 게시판·사람 머지·`sync_prs`가 그대로 다룬다."""
    x, cr = _task(svc.state, task_id)
    _mine(cr, who)
    if any(p.vcs_number == number and _same_conn(p, cr.connection) for p in svc.state.prs):
        raise _error("PR_TAKEN", f"형상관리 변경 #{number}는 이미 다른 작업에 이어져 있다")
    # 형상관리 읽기는 잠금 밖에서 (sync_prs와 같다)
    try:
        adapter = vcs_bridge.active(cr.connection)
    except VcsError as exc:
        raise _error("NO_VCS", f"연결 {cr.connection}을 쓸 수 없다 ({exc.code})") from exc
    if adapter is None:
        raise _error("NO_VCS", "형상관리 연결이 없다")
    try:
        head = await adapter.describe_change(number)
        change = await adapter.get_change(number)
    except VcsError as exc:
        raise _error("PR_NOT_FOUND", f"형상관리 변경 #{number}를 읽지 못했다 ({exc.code})") from exc
    if head.state == "closed":
        raise _error("INVALID_INPUT", f"변경 #{number}는 닫혀 있다")
    if not _title_has(head.title, task_id):
        raise _error("INVALID_INPUT", f"PR 제목에 작업 id {task_id}를 넣어야 잇는다")
    rows = [
        CodingFile(path=f.path, additions=f.additions, deletions=f.deletions) for f in change.files
    ]
    box: dict[str, Any] = {}

    async def run(s: State) -> None:
        x, cr = _task(s, task_id)
        _mine(cr, who)
        ctx = _ctx(svc, s, x)
        n = len(s.prs) + 30  # 서버 에이전트 PR과 같은 표시 번호 규칙
        while any(p.id == f"PR #{n}" for p in s.prs):
            n += 1
        pr_id = f"PR #{n}"
        paths = sorted(vcs.logical_paths(cr.connection, [r.path for r in rows]))
        lines, files = coder.total_lines(rows), len(rows)
        area = coder.area_of(paths)
        gen_text = f"{pr_id} 개발자 Claude #{number} {lines}줄·{files}파일·{area} → 코드 규칙 확인"
        t = svc.clock()
        refused = None
        try:
            await svc._execute(
                "generate_pr", ctx, f"{pr_id}:sub{number}", gen_text,
                vcs_bridge.pr_size_params(lines, files, paths, x.id),
            )  # fmt: skip
        except GateRefused as exc:
            refused = exc.decision
        pr = PR(
            id=pr_id,
            number=n,
            task=x.id,
            title=head.title,
            branch=head.branch,
            reviews=[],
            gate=PrGate(
                result="C 보류" if refused else "통과", lines=lines, files=files, paths=area
            ),
            merged=False,
            changed_paths=paths,
            vcs_number=number,
            url=head.url or url,
            target=svc._coding_target(cr.connection),
        )
        s.prs.append(pr)
        s.activity.append(
            svc._act(t, ctx, "dev", "safety", "execute(generate_pr)", "execute", gen_text, "B")
        )
        cr.pr = pr_id
        cr.files = rows
        cr.lease_until = None  # PR이 열리면 임대 대신 PR 맞추기(sync_prs)가 본다
        cr.updated_at = t
        if refused is not None:
            cr.status = "waiting_human"
            cr.error = f"실행 관문이 generate_pr를 허락하지 않음 ({refused.status}) → 사람 확인"
            x.state = "C 보류"
            svc._hold(pr, WAIT_HUMAN, pr_check(s, pr), refused)
        else:
            cr.status = "opened"
            cr.error = None
            x.state = "PR 검수"
        line = f"{task_id} {who} PR 보고 → {pr_id} (형상관리 #{number}, {head.branch})"
        s.activity.append(svc._act(t, ctx, "human", "dev", "work.pr", "API", line))
        box["out"] = {**spec_of(s, x), "pr_id": pr_id, "vcs_number": number, "url": pr.url}

    await svc._apply(run)
    return box["out"]


def _same_conn(p: PR, connection: str | None) -> bool:
    return (p.target.connection_id if p.target else None) in (connection, None)


async def _requeue(svc: PlanningService, s: State, x: Task, cr: CodingRun, why: str) -> None:
    ctx = _ctx(svc, s, x)
    now = svc.clock()
    text = f"{x.id} {why} → 개발자 Claude 대기열로"
    n = sum(1 for g in svc.gate_calls if g.action == "dev_task_release") + 1
    await svc._execute(
        "dev_task_release", ctx, f"{x.id}:{n}", text, {"claimed_by": cr.claimed_by, "why": why}
    )
    s.activity.append(svc._act(now, ctx, "human", "dev", "dev_task_release", "사람", text))
    cr.status = "queued"
    cr.claimed_by = None
    cr.claimed_at = None
    cr.lease_until = None
    cr.error = why
    cr.updated_at = now
    x.owner = "개발자 Claude"


async def release(
    svc: PlanningService, task_id: str, who: str, reason: str | None = None
) -> dict[str, Any]:
    """가져간 작업을 내려놓는다 (다른 개발자가 가져갈 수 있게 대기열로)."""
    why = " ".join(str(reason or "").split())[:200]
    box: dict[str, Any] = {}

    async def run(s: State) -> None:
        x, cr = _task(s, task_id)
        _mine(cr, who)
        await _requeue(svc, s, x, cr, f"{who} 반납" + (f": {why}" if why else ""))
        box["spec"] = spec_of(s, x)

    await svc._apply(run)
    return box["spec"]


async def expire_leases(svc: PlanningService) -> int:
    """임대가 끝나도록 진행 보고가 없는 구독형 작업을 대기열로 돌린다. 돌린 개수."""
    now = svc.clock()
    stale = {
        x.id for x in svc.state.tasks
        if x.coding and x.coding.mode == SUBSCRIPTION and _expired(x.coding, now)
    }  # fmt: skip
    if not stale:
        return 0

    async def run(s: State) -> None:
        for x in s.tasks:
            cr = x.coding
            if x.id in stale and cr is not None and _expired(cr, svc.clock()):
                mins = lease_minutes()
                await _requeue(svc, s, x, cr, f"{cr.claimed_by} {mins}분 동안 진행 보고 없음")

    await svc._apply(run)
    return len(stale)
