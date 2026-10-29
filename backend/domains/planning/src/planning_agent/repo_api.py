"""레포 작업 공간 REST (`/api/planning/repo/*`). 개발 화면에서 PR·브랜치를 직접 다룬다.

- 연결된 레포의 PR을 사람이 직접 올린 것까지 모두 보여 준다. AI가 만든 PR(내부 PR과 번호가 이어진
  것)은 `internal`에 내부 PR id를 붙인다.
- 연결은 `?connection=<id>`로 고른다. 없으면 첫 연결. 연결이 없으면 409 `NO_VCS`.
- 읽기(GET)는 열어 두고, 쓰기(POST)는 사람 토큰(auth.py HUMAN)만 받는다.
- 쓰기는 모두 실행 관문 뒤에만 형상관리를 부른다.
  AI PR 머지는 내부 `mergePr` 흐름 그대로(감사 기록), 사람이 올린 PR 머지는 `merge_pr`,
  나머지는 `repo_*` 액션(safety registry.toml).
- 토큰 값은 어느 응답·로그에도 넣지 않는다 (부품 오류 메시지는 토큰을 가린 것만).
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from planning_agent import vcs, vcs_bridge
from planning_agent.auth import require_human
from planning_agent.domain.autocheck import merge_risk_paths
from planning_agent.domain.models import PR
from planning_agent.domain.service import ActionError, GateRefused, PlanningService
from planning_agent.vcs.base import (
    CHANGE_LIST_LIMIT,
    GRAPH_BRANCHES,
    GRAPH_LIMIT,
    GRAPH_MAX,
    ISSUE_LIST_LIMIT,
    ISSUE_STATES,
    MERGE_METHODS,
    Change,
    Graph,
    VcsAdapter,
    VcsError,
)

HUMAN = [Depends(require_human)]
MAX_LIST = 100
MAX_COMMENT = 65_000
MAX_TITLE = 256
STATES = ("open", "merged", "closed", "all")
# 부품 오류 → HTTP 상태
VCS_STATUS = {
    "not_found": 404,
    "conflict": 409,
    "read_only": 409,
    "invalid": 400,
    "rate_limited": 429,
}


class RepoError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _plain(x: Any) -> Any:
    return dataclasses.asdict(x) if dataclasses.is_dataclass(x) and not isinstance(x, type) else x


def _resolve(connection: str | None) -> tuple[str, VcsAdapter, vcs.Connection]:
    if not vcs_bridge.enabled():
        raise RepoError(409, "NO_VCS", "연결된 레포가 없다")
    conns = vcs.routing_connections()
    cid = (connection or "").strip() or conns[0].id
    conn = next((c for c in conns if c.id == cid), None)
    if conn is None:
        raise RepoError(404, "NOT_FOUND", f"연결 {cid}가 없다")
    return cid, vcs.get_adapter(cid), conn


def _first_id() -> str | None:
    conns = vcs.routing_connections()
    return conns[0].id if conns else None


def _linked(svc: PlanningService, cid: str) -> dict[int, PR]:
    """형상관리 번호 → 내부 PR (AI가 만든 PR). 연결을 모르는 PR은 첫 연결로 본다."""
    first = _first_id()
    out: dict[int, PR] = {}
    for p in svc.state.prs:
        if p.vcs_number is None:
            continue
        owner = p.target.connection_id if p.target is not None else first
        if owner == cid:
            out[p.vcs_number] = p
    return out


def _internal(p: PR | None) -> dict | None:
    if p is None:
        return None
    return {"id": p.id, "task": p.task, "merged": p.merged, "closed": p.closed}


def _repo_meta(cid: str, conn: vcs.Connection, adapter: VcsAdapter) -> dict:
    return {
        "connection": cid,
        "repo": conn.repo or str(getattr(adapter, "repo", "") or ""),
        "base_branch": str(getattr(adapter, "base_branch", "") or conn.base_branch),
        "read_only": adapter.capabilities.read_only,
        "issues": adapter.capabilities.issues,
    }


def _size_params(cid: str, change: Change) -> dict:
    """`merge_pr`·`repo_revert_pr` params: 내부 PR과 같은 크기·경로 키 (안전 관문 코드 규칙)."""
    paths = sorted(set(vcs.logical_paths(cid, change.changed_paths)))
    return {
        "changed_lines": change.additions + change.deletions,
        "changed_files": len(change.files),
        "changed_paths": paths,
        "review_mismatch_count": 0,
        "linked_incident_count": 0,
        "irreversible_count": len(merge_risk_paths(paths)),
        "auto": False,
        "source": "repo",
    }


def _graph_rows(graph: Graph, linked: dict[int, PR]) -> list[dict]:
    """커밋마다 브랜치 끝 꼬리표(refs)와 PR 꼬리표(prs)를 붙인다.
    PR은 머지됐으면 머지 커밋에, 아니면 head 끝 커밋에 단다."""
    refs: dict[str, list[str]] = {}
    for name, sha in graph.heads.items():
        refs.setdefault(sha, []).append(name)
    prs: dict[str, list[dict]] = {}
    for p in graph.prs:
        at = (p.merge_sha or p.head_sha) if p.state == "merged" else p.head_sha
        if not at:
            continue
        internal = linked.get(p.number)
        prs.setdefault(at, []).append(
            {
                "number": p.number,
                "state": p.state,
                "title": p.title,
                "branch": p.branch,
                "internal": internal.id if internal else None,
            }
        )
    return [
        {
            **_plain(c),
            "parents": list(c.parents),
            "refs": refs.get(c.sha, []),
            "prs": prs.get(c.sha, []),
        }
        for c in graph.commits
    ]


async def _body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _issues(adapter: VcsAdapter) -> None:
    if not adapter.capabilities.issues:
        raise RepoError(409, "NO_ISSUES", "이 형상관리 연결에는 이슈가 없다")


def _writable(adapter: VcsAdapter) -> None:
    if adapter.capabilities.read_only:
        raise RepoError(409, "READ_ONLY", "읽기 전용 연결이라 쓰지 않는다")


def add_repo_routes(router: APIRouter, get_service: Callable[[], PlanningService]) -> None:
    def ok(data: Any, **meta: Any) -> dict:
        svc = get_service()
        return {"data": data, "meta": {"tenant_id": svc.tenant_id, "version": svc.version, **meta}}

    async def guard(fn: Callable[[], Awaitable[dict]]) -> dict | JSONResponse:
        try:
            return await fn()
        except RepoError as exc:
            return _err(exc.status, exc.code, exc.message)
        except VcsError as exc:
            status = VCS_STATUS.get(exc.code, 502)
            return _err(status, f"VCS_{exc.code.upper()}", exc.message)
        except GateRefused as exc:
            return _err(
                409,
                "GATE_REJECTED",
                f"실행 관문이 {exc.action}을 허락하지 않아 실행하지 않았다 ({exc.decision.status})",
            )
        except ActionError as exc:
            return _err(400, exc.code, exc.message)

    # ---- 읽기

    @router.get("/repo/prs", response_model=None)
    async def list_repo_prs(
        connection: str | None = None,
        state: str = "open",
        q: str | None = None,
        limit: int = CHANGE_LIST_LIMIT,
    ) -> dict | JSONResponse:
        async def run() -> dict:
            if state not in STATES:
                raise RepoError(400, "INVALID_INPUT", f"state는 {', '.join(STATES)} 중 하나다")
            cid, adapter, conn = _resolve(connection)
            n = max(1, min(int(limit), MAX_LIST))
            rows = await adapter.list_changes(state, (q or "").strip()[:200] or None, n)
            linked = _linked(get_service(), cid)
            items = []
            for c in rows:
                item = _plain(c)
                item.pop("body", None)
                item["internal"] = _internal(linked.get(c.number))
                items.append(item)
            meta = _repo_meta(cid, conn, adapter)
            data = {**meta, "state": state, "items": items}
            return ok(data, count=len(items), more=len(rows) >= n)

        return await guard(run)

    @router.get("/repo/prs/{number}", response_model=None)
    async def get_repo_pr(number: int, connection: str | None = None) -> dict | JSONResponse:
        async def run() -> dict:
            cid, adapter, conn = _resolve(connection)

            async def checks() -> dict | None:
                try:
                    return _plain(await adapter.get_checks(number))
                except VcsError:
                    return None

            head, diff, commits, comments, ci = await asyncio.gather(
                adapter.describe_change(number),
                adapter.get_diff(number),
                adapter.list_commits(number),
                adapter.list_comments(number),
                checks(),
            )
            return ok(
                {
                    **_repo_meta(cid, conn, adapter),
                    "pr": _plain(head),
                    "internal": _internal(_linked(get_service(), cid).get(number)),
                    "diff": _plain(diff),
                    "commits": [_plain(c) for c in commits],
                    "comments": [_plain(c) for c in comments],
                    "checks": ci,
                }
            )

        return await guard(run)

    # 이슈: 읽기와 코멘트. 코멘트는 PR 코멘트와 같은 repo_comment 관문을 지난다
    @router.get("/repo/issues", response_model=None)
    async def list_repo_issues(
        connection: str | None = None,
        state: str = "open",
        q: str | None = None,
        limit: int = ISSUE_LIST_LIMIT,
    ) -> dict | JSONResponse:
        async def run() -> dict:
            if state not in ISSUE_STATES:
                raise RepoError(
                    400, "INVALID_INPUT", f"state는 {', '.join(ISSUE_STATES)} 중 하나다"
                )
            cid, adapter, conn = _resolve(connection)
            _issues(adapter)
            n = max(1, min(int(limit), MAX_LIST))
            rows = await adapter.list_issues(state, (q or "").strip()[:200] or None, n)
            items = []
            for i in rows:
                item = _plain(i)
                item.pop("body", None)
                items.append(item)
            data = {**_repo_meta(cid, conn, adapter), "state": state, "items": items}
            return ok(data, count=len(items), more=len(rows) >= n)

        return await guard(run)

    @router.get("/repo/issues/{number}", response_model=None)
    async def get_repo_issue(number: int, connection: str | None = None) -> dict | JSONResponse:
        async def run() -> dict:
            cid, adapter, conn = _resolve(connection)
            _issues(adapter)
            head, comments = await asyncio.gather(
                adapter.describe_issue(number), adapter.list_comments(number)
            )
            return ok(
                {
                    **_repo_meta(cid, conn, adapter),
                    "issue": _plain(head),
                    "comments": [_plain(c) for c in comments],
                }
            )

        return await guard(run)

    @router.post("/repo/issues/{number}/comment", response_model=None, dependencies=HUMAN)
    async def comment_repo_issue(
        number: int, request: Request, connection: str | None = None
    ) -> dict | JSONResponse:
        async def run() -> dict:
            body = await _body(request)
            text = body.get("body")
            if not isinstance(text, str) or not text.strip() or len(text) > MAX_COMMENT:
                raise RepoError(400, "INVALID_INPUT", f"body는 1~{MAX_COMMENT}자 문자열이다")
            cid, adapter, _ = _resolve(connection)
            _issues(adapter)
            _writable(adapter)
            await adapter.describe_issue(number)  # 없는 이슈·PR 번호면 여기서 404
            made = await get_service().repo_write(
                "repo_comment",
                f"{cid}#{number}",
                f"사람이 레포 이슈 #{number}에 코멘트",
                {"chars": len(text)},
                lambda: adapter.add_comment(number, text),
            )
            return ok({"comment": _plain(made)})

        return await guard(run)

    @router.get("/repo/branches", response_model=None)
    async def list_repo_branches(connection: str | None = None) -> dict | JSONResponse:
        async def run() -> dict:
            cid, adapter, conn = _resolve(connection)
            branches, prs = await asyncio.gather(
                adapter.list_branches(), adapter.list_changes("all", None, MAX_LIST)
            )
            meta = _repo_meta(cid, conn, adapter)
            by_branch: dict[str, Any] = {}
            for c in prs:  # 최근 갱신 순. 열린 PR이 있으면 그것, 없으면 가장 최근 PR
                hit = by_branch.get(c.branch)
                if hit is None or (c.state == "open" and hit.state != "open"):
                    by_branch[c.branch] = c
            items = []
            for b in branches:
                pr = by_branch.get(b.name)
                items.append(
                    {
                        **_plain(b),
                        "base": b.name == meta["base_branch"],
                        "pr": {"number": pr.number, "state": pr.state, "title": pr.title}
                        if pr
                        else None,
                    }
                )
            items.sort(key=lambda x: (not (x["default"] or x["base"]), x["name"]))
            return ok({**meta, "items": items}, count=len(items))

        return await guard(run)

    @router.get("/repo/graph", response_model=None)
    async def repo_graph(
        connection: str | None = None,
        branches: str | None = None,
        limit: int = GRAPH_LIMIT,
    ) -> dict | JSONResponse:
        async def run() -> dict:
            names = list(dict.fromkeys(b.strip() for b in (branches or "").split(",") if b.strip()))
            if len(names) > GRAPH_BRANCHES or any(len(b) > 255 for b in names):
                raise RepoError(
                    400, "INVALID_INPUT", f"branches는 {GRAPH_BRANCHES}개까지 쉼표로 적는다"
                )
            n = max(1, min(int(limit), GRAPH_MAX))
            cid, adapter, conn = _resolve(connection)
            graph = await adapter.list_graph(names or None, n)
            meta = _repo_meta(cid, conn, adapter)
            rows = _graph_rows(graph, _linked(get_service(), cid))
            data = {
                **meta,
                "commits": rows,
                "heads": dict(graph.heads),
                "selected": list(graph.heads),
                "branches": [
                    {**_plain(b), "base": b.name == meta["base_branch"]} for b in graph.branches
                ],
                "truncated": graph.truncated,
                "limit": n,
            }
            return ok(data, count=len(rows), more=graph.truncated)

        return await guard(run)

    @router.get("/repo/commits/{sha}", response_model=None)
    async def get_repo_commit(sha: str, connection: str | None = None) -> dict | JSONResponse:
        async def run() -> dict:
            cid, adapter, conn = _resolve(connection)
            got = await adapter.get_commit(sha)
            commit = _plain(got)
            commit["parents"] = list(got.parents)
            return ok({**_repo_meta(cid, conn, adapter), "commit": commit})

        return await guard(run)

    # ---- 쓰기 (사람 토큰)

    @router.post("/repo/prs/{number}/merge", response_model=None, dependencies=HUMAN)
    async def merge_repo_pr(
        number: int, request: Request, connection: str | None = None
    ) -> dict | JSONResponse:
        async def run() -> dict:
            body = await _body(request)
            method = str(body.get("method") or "merge")
            if method not in MERGE_METHODS:
                raise RepoError(400, "INVALID_INPUT", f"method는 {', '.join(MERGE_METHODS)}")
            cid, adapter, _ = _resolve(connection)
            _writable(adapter)
            svc = get_service()
            linked = _linked(svc, cid).get(number)
            if linked is not None and not linked.merged:
                # AI가 만든 PR: 내부 사람 머지 흐름 그대로 (관문·감사 기록·작업 완료 처리)
                await svc.dispatch({"type": "merge_pr", "pr": linked.id})
                after = next((p for p in svc.state.prs if p.id == linked.id), None)
                if after is None or not after.merged:
                    raise RepoError(502, "VCS_MERGE_FAILED", f"{linked.id} 머지하지 못했다")
                head = await adapter.describe_change(number)
                return ok({"pr": _plain(head), "via": "internal", "internal": _internal(after)})
            change = await adapter.get_change(number)
            if change.merged:
                raise RepoError(409, "VCS_CONFLICT", f"이미 병합된 변경: {number}")
            text = f"사람이 레포 PR #{number} 머지 ({method})"
            await svc.repo_write(
                "merge_pr",
                f"{cid}#{number}",
                text,
                _size_params(cid, change),
                lambda: adapter.merge_change(number, method),
            )
            head = await adapter.describe_change(number)
            return ok({"pr": _plain(head), "via": "repo", "internal": None})

        return await guard(run)

    @router.post("/repo/prs/{number}/close", response_model=None, dependencies=HUMAN)
    async def close_repo_pr(number: int, connection: str | None = None) -> dict | JSONResponse:
        async def run() -> dict:
            cid, adapter, _ = _resolve(connection)
            _writable(adapter)
            svc = get_service()
            linked = _linked(svc, cid).get(number)
            task = next((t for t in svc.state.tasks if linked and t.id == linked.task), None)
            if linked is not None and task is not None and task.coding is not None:
                # AI 코딩 작업 PR: 작업 중단 흐름 그대로 (실행을 멈추고 PR을 닫는다)
                await svc.cancel_dev_task(task.id)
            else:
                await svc.repo_write(
                    "repo_close_pr",
                    f"{cid}#{number}",
                    f"사람이 레포 PR #{number} 닫기",
                    None,
                    lambda: adapter.close_change(number),
                )
            head = await adapter.describe_change(number)
            after = _linked(svc, cid).get(number)
            return ok({"pr": _plain(head), "internal": _internal(after)})

        return await guard(run)

    @router.post("/repo/prs/{number}/reopen", response_model=None, dependencies=HUMAN)
    async def reopen_repo_pr(number: int, connection: str | None = None) -> dict | JSONResponse:
        async def run() -> dict:
            cid, adapter, _ = _resolve(connection)
            _writable(adapter)
            svc = get_service()
            linked = _linked(svc, cid).get(number)
            if linked is not None and linked.closed:
                raise RepoError(409, "INTERNAL_FLOW", f"{linked.id}는 중단한 AI 작업이다")
            head = await svc.repo_write(
                "repo_reopen_pr",
                f"{cid}#{number}",
                f"사람이 레포 PR #{number} 다시 열기",
                None,
                lambda: adapter.reopen_change(number),
            )
            return ok({"pr": _plain(head)})

        return await guard(run)

    @router.post("/repo/prs/{number}/comment", response_model=None, dependencies=HUMAN)
    async def comment_repo_pr(
        number: int, request: Request, connection: str | None = None
    ) -> dict | JSONResponse:
        async def run() -> dict:
            body = await _body(request)
            text = body.get("body")
            if not isinstance(text, str) or not text.strip() or len(text) > MAX_COMMENT:
                raise RepoError(400, "INVALID_INPUT", f"body는 1~{MAX_COMMENT}자 문자열이다")
            cid, adapter, _ = _resolve(connection)
            _writable(adapter)
            made = await get_service().repo_write(
                "repo_comment",
                f"{cid}#{number}",
                f"사람이 레포 PR #{number}에 코멘트",
                {"chars": len(text)},
                lambda: adapter.add_comment(number, text),
            )
            return ok({"comment": _plain(made)})

        return await guard(run)

    @router.post("/repo/prs/{number}/revert", response_model=None, dependencies=HUMAN)
    async def revert_repo_pr(
        number: int, request: Request, connection: str | None = None
    ) -> dict | JSONResponse:
        async def run() -> dict:
            body = await _body(request)
            title = body.get("title")
            if title is not None and (not isinstance(title, str) or len(title) > MAX_TITLE):
                raise RepoError(400, "INVALID_INPUT", f"title은 {MAX_TITLE}자까지 문자열이다")
            cid, adapter, _ = _resolve(connection)
            _writable(adapter)
            change = await adapter.get_change(number)
            if not change.merged:
                raise RepoError(409, "VCS_CONFLICT", f"병합되지 않은 변경: {number}")
            made = await get_service().repo_write(
                "repo_revert_pr",
                f"{cid}#{number}",
                f"사람이 레포 PR #{number} 되돌리기 PR 열기",
                _size_params(cid, change),
                lambda: adapter.revert_change(number, (title or "").strip() or None),
            )
            return ok({"revert": _plain(made)})

        return await guard(run)

    @router.post("/repo/branches/delete", response_model=None, dependencies=HUMAN)
    async def delete_repo_branch(
        request: Request, connection: str | None = None
    ) -> dict | JSONResponse:
        async def run() -> dict:
            body = await _body(request)
            name = body.get("name")
            if not isinstance(name, str) or not name.strip():
                raise RepoError(400, "INVALID_INPUT", "name은 브랜치 이름 문자열이다")
            name = name.strip()
            cid, adapter, conn = _resolve(connection)
            _writable(adapter)
            meta = _repo_meta(cid, conn, adapter)
            branches = {b.name: b for b in await adapter.list_branches()}
            b = branches.get(name)
            if name == meta["base_branch"] or (b is not None and (b.default or b.protected)):
                raise RepoError(400, "PROTECTED_BRANCH", f"기준·기본·보호 브랜치: {name}")
            if b is None:
                raise RepoError(404, "NOT_FOUND", f"브랜치 {name}이 없다")
            open_prs = await adapter.list_changes("open", None, MAX_LIST)
            opened = [c for c in open_prs if c.branch == name]
            if opened:
                raise RepoError(409, "OPEN_PR", f"열린 PR #{opened[0].number}의 브랜치다")
            await get_service().repo_write(
                "repo_delete_branch",
                f"{cid}:{name}",
                f"사람이 브랜치 {name} 지우기",
                {"branch": name},
                lambda: adapter.delete_branch(name),
            )
            return ok({"deleted": name})

        return await guard(run)
