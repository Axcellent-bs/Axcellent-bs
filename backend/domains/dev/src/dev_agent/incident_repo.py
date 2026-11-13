"""장애 대응에서 실제 레포를 다루는 일 (인프라 진단·되돌리기).

- `repo_related`: `find_related_prs`가 메모리 PR에 더해, 형상관리 연결의 레포에서 기준 브랜치에
  머지된 PR을 읽어 구간 안 후보를 낸다. 후보에는 `repo`·`connection_id`·`merge_sha`가 붙는다.
- `revert_merged`: `revert_merged_pr`(infra → dev, B). 병합된 PR의 되돌리기 PR을 열고 머지까지 간다.
  새 관문 액션을 만들지 않고 레포 작업 공간과 같은
  `repo_revert_pr`(B) → `merge_pr`(B) 관문을 거친다.
  CI를 기다려야 하면 자동 머지를 예약한다. 같은 연결·PR을 다시 부르면 이미 연 되돌리기 PR을 쓴다.

원인은 판정하지 않는다. 어떤 PR을 되돌릴지는 요청한 쪽(인프라 진단)이 정한다.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Literal

from ax_contracts.schemas.planning import RelatedPr, RevertMergedPrOutput
from ax_workflow.core import ActionError, ActionResult, GateRefused
from ax_workflow.handover import area_of_path
from ax_workflow.models import PR, State
from dev_agent import vcs
from dev_agent.service import DevService
from dev_agent.vcs.base import Change, VcsAdapter, VcsError

REPO_TIMEOUT_SECONDS = 15.0  # 연결 하나를 읽는 데 기다리는 시간
MERGE_METHOD = "squash"
_locks: dict[str, asyncio.Lock] = {}


# ---------------------------------------------------------------- 관련 PR


def _connection_matches(conn: vcs.Connection, component: str) -> bool:
    """연결 설명·담당 경로에 구성요소 이름이 들어 있나 (예: "결제 payment" 설명의 레포)."""
    key = component.strip().lower()
    if not key:
        return False
    text = " ".join([conn.description, *conn.paths]).lower()
    return key in text


def _component_ok(cid: str, conn: vcs.Connection, change: Change, component: str | None) -> bool:
    if not component or _connection_matches(conn, component):
        return True
    paths = vcs.logical_paths(cid, change.changed_paths)
    return any(p.startswith(component) or area_of_path(p) == component for p in paths)


async def _repo_changes(
    cid: str, window_from: datetime, window_to: datetime
) -> tuple[VcsAdapter, list[Change]]:
    adapter = vcs.get_adapter(cid)
    found = await asyncio.wait_for(
        adapter.find_changes(window_from, window_to), REPO_TIMEOUT_SECONDS
    )
    return adapter, found


async def repo_related(
    svc: DevService,
    *,
    context_id: str,
    requester: str,
    window_from: datetime,
    window_to: datetime,
    component: str | None,
    memory: list[tuple[PR, str | None, str | None]],
) -> tuple[list[RelatedPr], list[str]]:
    """메모리 후보(`memory`)와 연결 레포 후보를 합친다. (후보, 읽지 못한 연결 id).

    같은 레포 PR을 가리키는 메모리 PR(연결·번호가 같음)은 레포 후보 하나로 합치고 기획서·문단을
    옮겨 붙인다. 최근 머지 순."""
    conns = vcs.routing_connections()
    linked: dict[tuple[str, int], tuple[PR, str | None, str | None]] = {}
    for row in memory:
        p = row[0]
        cid = p.target.connection_id if p.target else None
        if cid and p.vcs_number:
            linked[(cid, p.vcs_number)] = row
    got = await asyncio.gather(
        *(_repo_changes(c.id, window_from, window_to) for c in conns), return_exceptions=True
    )
    rows: list[RelatedPr] = []
    used: set[str] = set()
    failed: list[str] = []
    for conn, res in zip(conns, got, strict=True):
        if isinstance(res, BaseException):
            if not isinstance(res, VcsError | TimeoutError):
                raise res
            failed.append(conn.id)
            continue
        adapter, changes = res
        repo = conn.repo or str(getattr(adapter, "repo", "") or "") or None
        for ch in changes:
            if ch.merged_at is None or not _component_ok(conn.id, conn, ch, component):
                continue
            mem = linked.get((conn.id, ch.number))
            if mem is not None:
                used.add(mem[0].id)
            rows.append(
                RelatedPr(
                    pr_number=ch.number,
                    title=ch.title,
                    merged_at=ch.merged_at,
                    spec_id=mem[1] if mem else None,
                    para_id=mem[2] if mem else None,
                    changed_paths=vcs.logical_paths(conn.id, ch.changed_paths),
                    repo=repo,
                    connection_id=conn.id,
                    merge_sha=ch.merge_sha,
                )
            )
    rows += [
        RelatedPr(
            pr_number=p.number,
            title=p.title,
            merged_at=p.merged_at,
            spec_id=spec,
            para_id=para,
            changed_paths=list(p.changed_paths),
        )
        for p, spec, para in memory
        if p.id not in used and p.merged_at is not None
    ]
    rows.sort(key=lambda r: (r.merged_at, r.pr_number), reverse=True)
    real = [r for r in rows if r.connection_id]
    if real or failed:
        await _log(
            svc, context_id, "planning", requester, "find_related_prs", _related_text(real, failed)
        )
    return rows, failed


def _related_text(real: list[RelatedPr], failed: list[str]) -> str:
    names = ", ".join(f"{r.connection_id}#{r.pr_number}" for r in real) or "없음"
    text = f"레포 머지 PR 후보: {names}"
    if failed:
        text += f" · 읽지 못한 연결 {', '.join(failed)}"
    return text + " · 원인 판정은 하지 않음"


async def _log(
    svc: DevService, ctx: str, frm: str, to: str, point: str, text: str, grade: str = "read"
) -> None:
    async def add(s: State) -> None:
        s.activity.append(svc._act(svc.clock(), ctx, frm, to, point, "A2A", text, grade))

    await svc._apply(add)


# ---------------------------------------------------------------- 되돌리기


Status = Literal["merged", "auto_merge_scheduled", "held"]
_REVERTS = re.compile(r"Reverts\s+\S*#(\d+)\b")


async def find_revert(adapter: VcsAdapter, number: int) -> int | None:
    """이미 연 되돌리기 PR (닫힌 것은 빼고).

    본문으로 찾는다: GitHub `Reverts owner/repo#N`, 목 부품 `Reverts #N`."""
    for c in await adapter.list_changes(state="all"):
        if c.state == "closed" or not c.title.startswith("Revert"):
            continue
        if any(int(m) == number for m in _REVERTS.findall(c.body or "")):
            return c.number
    return None


async def _gated(
    svc: DevService,
    action: str,
    ctx: str,
    target: str,
    text: str,
    params: dict,
    op: Callable[[], Awaitable[Any]],
    requester: str,
) -> Any:
    """관문을 지난 뒤에만 형상관리를 부른다. 관문 키는 연결·PR로 정해 다시 불러도 같다."""
    box: dict[str, Any] = {}

    async def run(s: State) -> ActionResult:
        await svc._execute(action, ctx, target, text, params)
        try:
            box["result"] = await op()
        except VcsError as exc:
            box["error"] = exc
            note = f"{text} 실패 ({exc.code})"
            s.activity.append(
                svc._act(svc.clock(), ctx, "dev", "dev", f"vcs.{action}_failed", "API", note)
            )
            return ActionResult(outcome="failed")
        s.activity.append(svc._act(svc.clock(), ctx, requester, "dev", action, "A2A", text, "B"))
        return ActionResult(outcome=action)

    await svc._apply(run)
    if "error" in box:
        raise box["error"]
    return box.get("result")


def _out(number: int | None, status: Status, change: Change | None = None) -> RevertMergedPrOutput:
    merged = bool(change and change.merged)
    return RevertMergedPrOutput(
        revert_pr_number=number,
        merged=merged,
        merge_sha=change.merge_sha if merged and change else None,
        status=status,
    )


async def revert_merged(
    svc: DevService,
    *,
    context_id: str,
    requester: str,
    connection_id: str,
    pr_number: int,
    reason: str,
    incident_context_id: str | None = None,
) -> RevertMergedPrOutput:
    """병합된 PR을 되돌리는 PR을 열고 머지까지 간다. 관문이 막으면 `held`.

    `ActionError`: 모르는 연결(NOT_FOUND)·읽기 전용(READ_ONLY)·병합 안 된 PR(NOT_MERGED).
    형상관리 오류는 `VcsError`로 그대로 던진다."""
    from dev_agent.repo_api import _size_params  # repo_api가 service를 불러 순환을 피한다

    if vcs.find_connection(connection_id) is None:
        raise ActionError("NOT_FOUND", f"모르는 연결: {connection_id}")
    adapter = vcs.get_adapter(connection_id)
    if adapter.capabilities.read_only:
        raise ActionError("READ_ONLY", f"연결 {connection_id}는 읽기 전용이다")
    key = f"{connection_id}#{pr_number}"
    ctx = incident_context_id or context_id
    why = " ".join(reason.split())[:200]
    async with _locks.setdefault(key, asyncio.Lock()):
        src = await adapter.get_change(pr_number)
        if not src.merged:
            raise ActionError("NOT_MERGED", f"병합되지 않은 PR: {key}")
        number = await find_revert(adapter, pr_number)
        if number is None:
            try:
                made = await _gated(
                    svc,
                    "repo_revert_pr",
                    ctx,
                    f"{key}:revert",
                    f"{requester} 요청으로 레포 PR #{pr_number} 되돌리기 PR 열기: {why}",
                    _size_params(connection_id, src),
                    lambda: adapter.revert_change(pr_number),
                    requester,
                )
            except GateRefused:
                return _out(None, "held")
            number = int(made.number)
        rev = await adapter.get_change(number)
        if rev.merged:
            return _out(number, "merged", rev)
        return await _merge(svc, adapter, ctx, connection_id, number, rev, why, requester)


async def _merge(
    svc: DevService,
    adapter: VcsAdapter,
    ctx: str,
    cid: str,
    number: int,
    rev: Change,
    why: str,
    requester: str,
) -> RevertMergedPrOutput:
    from dev_agent.repo_api import _size_params

    checks = (await adapter.get_checks(number)).state
    if checks == "failed":
        return _out(number, "held")  # 되돌리기 PR의 CI가 실패했다 → 사람이 본다
    now = checks != "pending"
    text = f"{requester} 요청으로 되돌리기 PR #{number} " + ("머지" if now else "자동 머지 예약")
    auto = getattr(adapter, "enable_auto_merge", None)

    async def op() -> Status:
        if now:
            try:
                await adapter.merge_change(number, MERGE_METHOD)
                return "merged"
            except VcsError:
                if auto is None:
                    raise
        if auto is None:
            raise VcsError("unavailable", "자동 머지를 걸 수 없는 형상관리다")
        await auto(number, MERGE_METHOD)
        return "auto_merge_scheduled"

    try:
        status = await _gated(
            svc,
            "merge_pr",
            ctx,
            f"{cid}#{number}:merge",
            f"{text}: {why}",
            _size_params(cid, rev),
            op,
            requester,
        )
    except GateRefused:
        return _out(number, "held")
    except VcsError:
        return _out(number, "held")
    if status == "merged":
        return _out(number, "merged", await adapter.get_change(number))
    return _out(number, "auto_merge_scheduled")
