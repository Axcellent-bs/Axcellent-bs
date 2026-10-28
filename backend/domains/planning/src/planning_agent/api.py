"""화면용 REST (`/api/planning/*`). 성공 `{data, meta}`, 실패 `{error: {code, message}}`.

- `GET` 목록·상세는 상태를 읽기만 한다.
- `POST /actions`는 프로토타입 Action(`{"type": "confirmSpec", "spec": "SPEC-7"}`)을 그대로 받는다
  (`confirm_spec`처럼 snake_case도 받는다). 사람 버튼(확정·머지·승급 승인)은 여기로만 받는다.
- `GET /events`는 SSE(`text/event-stream`). 연결하면 현재 상태를 한 번 보내고, 상태가 바뀔 때마다
  `event: state` + 전체 상태 JSON을 보낸다. 15초마다 주석 줄로 연결을 유지한다.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from planning_agent import coder, dev_chat, vcs
from planning_agent.deploy_store import get_deploy_store
from planning_agent.domain.deploy import DeployError
from planning_agent.domain.metrics import compute_metrics
from planning_agent.domain.models import dump
from planning_agent.domain.projects import project_detail, project_summary, sorted_projects
from planning_agent.domain.rules import AUTO_LEVELS, PROMOTE_AFTER, auto_limit
from planning_agent.domain.service import TEAM_LEAD, ActionError, GateRefused, PlanningService
from planning_agent.domain.trace import code_of, trace_of, trace_text

KEEPALIVE_SECONDS = 15.0
# 코딩 작업 오류 중 409로 주는 것 (설정·상태 문제). 나머지 입력 오류는 400
CODING_CONFLICT = {"NO_VCS", "LLM_NOT_CONFIGURED", "NOT_RETRYABLE", "WRONG_ROLE"}
MAX_CHATS_LISTED = 20


def _ok(data: Any, svc: PlanningService, **meta: Any) -> dict:
    return {"data": data, "meta": {"tenant_id": svc.tenant_id, "version": svc.version, **meta}}


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _list(items: list, svc: PlanningService) -> dict:
    data = [dump(x) for x in items]
    return _ok(data, svc, count=len(data))


def create_api_router(get_service: Callable[[], PlanningService]) -> APIRouter:
    router = APIRouter(prefix="/api/planning", tags=["planning"])

    @router.get("/state")
    def get_state() -> dict:
        svc = get_service()
        return _ok(svc.snapshot(), svc)

    @router.get("/requests")
    def list_requests(state: str | None = None) -> dict:
        svc = get_service()
        rows = [r for r in svc.state.requests if state is None or r.state == state]
        return _list(rows, svc)

    @router.get("/specs")
    def list_specs() -> dict:
        svc = get_service()
        return _list(svc.state.specs, svc)

    @router.get("/tasks")
    def list_tasks() -> dict:
        svc = get_service()
        return _list(svc.state.tasks, svc)

    @router.get("/pull-requests")
    def list_pull_requests() -> dict:
        svc = get_service()
        return _list(svc.state.prs, svc)

    @router.get("/threads")
    def list_threads() -> dict:
        svc = get_service()
        return _list(svc.state.threads, svc)

    @router.get("/decisions")
    def list_decisions() -> dict:
        svc = get_service()
        return _list(svc.state.decisions, svc)

    @router.get("/incident-links")
    def list_incident_links() -> dict:
        svc = get_service()
        return _list(svc.state.incidents, svc)

    @router.get("/docs")
    def list_docs() -> dict:
        svc = get_service()
        return _list(svc.state.docs, svc)

    @router.get("/metrics")
    def get_metrics() -> dict:
        svc = get_service()
        return _ok(compute_metrics(svc.state), svc)

    @router.get("/projects")
    def list_projects() -> dict:
        svc = get_service()
        rows = [project_summary(svc.state, r) for r in sorted_projects(svc.state)]
        return _ok(rows, svc, count=len(rows))

    @router.get("/projects/{project_id}", response_model=None)
    def get_project(project_id: str) -> dict | JSONResponse:
        svc = get_service()
        r = next((x for x in sorted_projects(svc.state) if x.id == project_id), None)
        if r is None:
            return _err(404, "NOT_FOUND", f"프로젝트 {project_id}가 없다")
        return _ok(project_detail(svc.state, r), svc)

    @router.get("/trace/{pr_id}", response_model=None)
    def get_trace(pr_id: str) -> dict | JSONResponse:
        """`28`, `PR-28`, `PR #28`(URL 인코딩 `PR%20%2328`) 모두 받는다."""
        svc = get_service()
        digits = "".join(ch for ch in pr_id if ch.isdigit())
        pr = next((p for p in svc.state.prs if digits and p.number == int(digits)), None)
        if pr is None:
            return _err(404, "NOT_FOUND", f"PR {pr_id}가 없다")
        steps = trace_of(svc.state, pr.id)
        return _ok(
            {
                "pr": pr.id,
                "code": [c.model_dump() for c in code_of(svc.state, pr)],
                "steps": [x.model_dump() for x in steps],
                "text": trace_text(steps),
            },
            svc,
        )

    @router.get("/review-queue")
    def list_review_queue(state: str | None = None) -> dict:
        """자동 확정·자동 머지를 멈춘 건 (답 대기·교차검증 대기·사람 확인).

        판단은 `decide_with`의 기존 사람 버튼(`POST /actions`)으로 한다.
        실제 교차검증자 연결·학습은 safety 협의 뒤 (#166 3번).
        """
        svc = get_service()
        rows: list[dict] = []
        for sp in svc.state.specs:
            if sp.status != "확정" and sp.auto_review is not None:
                rows.append(
                    {
                        "kind": "spec",
                        "id": sp.id,
                        "title": sp.title,
                        "action": "confirm_spec",
                        **dump(sp.auto_review),
                        "decide_with": {"type": "confirmSpec", "spec": sp.id},
                    }
                )
        for pr in svc.state.prs:
            if not pr.merged and pr.auto_review is not None:
                rows.append(
                    {
                        "kind": "pr",
                        "id": pr.id,
                        "title": pr.title,
                        "action": "merge_pr",
                        **dump(pr.auto_review),
                        "decide_with": {"type": "mergePr", "pr": pr.id},
                    }
                )
        if state is not None:
            rows = [r for r in rows if r["state"] == state]
        return _ok(rows, svc, count=len(rows))

    @router.get("/auto-policy")
    def get_auto_policy() -> dict:
        svc = get_service()
        pol = svc.state.auto_policy
        return _ok(
            {
                **dump(pol),
                "limit": auto_limit(pol),
                "levels": [lv.model_dump() for lv in AUTO_LEVELS],
                "promote_after": PROMOTE_AFTER,
                "linked_incidents": sum(1 for h in pol.history if h.result == "장애 연결"),
            },
            svc,
        )

    @router.post("/actions", response_model=None)
    async def post_action(request: Request) -> dict | JSONResponse:
        svc = get_service()
        try:
            body = await request.json()
        except ValueError:
            return _err(400, "INVALID_ACTION", "JSON이 아니다")
        try:
            result = await svc.dispatch(body)
        except ActionError as exc:
            status = 403 if exc.code == "SIMULATOR_DISABLED" else 400
            return _err(status, exc.code, exc.message)
        except GateRefused as exc:
            return _err(
                409,
                "GATE_REJECTED",
                f"실행 관문이 {exc.action}을 허락하지 않아 실행하지 않았다 ({exc.decision.status})",
            )
        return _ok(
            svc.snapshot(),
            svc,
            changed=result.changed,
            outcome=result.outcome,
            result=result.output,
        )

    @router.post("/requests/{request_id}/reject", response_model=None)
    async def post_reject_request(request_id: str, request: Request) -> dict | JSONResponse:
        """개선 초안 기각. 본문 `{decision_reason, alternatives_considered}` 둘 다 필수.

        `POST /actions` + `{"type": "rejectDraft", ...}`와 같은 길이다. 이유·대안이 없으면
        hr에 `decision_rejected`를 보내지 않고 400 `MISSING_DECISION_REASON`.
        """
        svc = get_service()
        try:
            body = await request.json()
        except ValueError:
            return _err(400, "INVALID_ACTION", "JSON이 아니다")
        if not isinstance(body, dict):
            return _err(400, "INVALID_ACTION", "본문은 객체여야 한다")
        if not any(r.id == request_id for r in svc.state.requests):
            return _err(404, "NOT_FOUND", f"요청 {request_id}가 없다")
        try:
            result = await svc.dispatch({**body, "type": "reject_draft", "request": request_id})
        except ActionError as exc:
            return _err(400, exc.code, exc.message)
        except GateRefused as exc:
            return _err(
                409,
                "GATE_REJECTED",
                f"실행 관문이 {exc.action}을 허락하지 않아 실행하지 않았다 ({exc.decision.status})",
            )
        return _ok(
            svc.snapshot(),
            svc,
            changed=result.changed,
            outcome=result.outcome,
            result=result.output,
        )

    # ---- 개발 도구 연결: 형상관리 (시스템 관리자 화면)

    @router.get("/vcs")
    def get_vcs_config() -> dict:
        return _ok(vcs.describe(), get_service())

    # CORS가 GET·POST만 열려 있어서 저장도 POST로 받는다
    @router.post("/vcs")
    async def put_vcs_config(request: Request) -> Any:
        body = await request.json()
        if not isinstance(body, dict):
            return _err(400, "INVALID_VCS", "본문은 객체여야 한다")
        try:
            vcs.configure(
                kind=str(body.get("kind") or ""),
                repo=str(body.get("repo") or ""),
                base_url=str(body.get("base_url") or ""),
                base_branch=str(body.get("base_branch") or ""),
                token=body.get("token") or None,
            )
        except vcs.VcsError as exc:
            return _err(400, "INVALID_VCS", exc.message)
        return _ok(vcs.describe(), get_service())

    @router.post("/vcs/test")
    async def post_vcs_test() -> dict:
        """연결 확인: 최근 하루 병합 목록을 읽어 본다 (쓰기 없음)."""
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        result: dict[str, Any] = {"kind": vcs.configured_kind(), "checked_at": now.isoformat()}
        try:
            changes = await vcs.get_vcs().find_changes(now - timedelta(days=1), now)
            result.update(ok=True, code=None, message=f"최근 하루 병합 {len(changes)}건 읽음")
        except vcs.VcsError as exc:
            result.update(ok=False, code=exc.code, message=exc.message)
        return _ok(result, get_service())

    # ---- 형상관리 연결 여러 개 (레포마다 하나). PR마다 AI가 연결을 제안하고 사람이 승인한다

    def _connections() -> dict:
        rows = vcs.list_connections()
        return _ok(rows, get_service(), count=len(rows))

    @router.get("/vcs/connections")
    def list_vcs_connections() -> dict:
        return _connections()

    @router.post("/vcs/connections", response_model=None)
    async def post_vcs_connection(request: Request) -> dict | JSONResponse:
        """연결 넣기·바꾸기. id가 없으면 새 연결(`conn-n`), 토큰을 비우면 그 연결의 토큰을 둔다."""
        body = await _body(request)
        if body is None:
            return _err(400, "INVALID_VCS", "본문은 JSON 객체여야 한다")
        paths = body.get("paths") or []
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            return _err(400, "INVALID_VCS", "paths는 문자열 목록이어야 한다")
        text = {
            k: body.get(k)
            for k in ("id", "kind", "repo", "base_url", "base_branch", "token", "description")
        }
        if any(v is not None and not isinstance(v, str) for v in text.values()):
            return _err(400, "INVALID_VCS", "문자열 필드에 다른 값이 들어 있다")
        try:
            vcs.upsert_connection(
                id=text["id"] or None,
                kind=text["kind"] or "",
                repo=text["repo"] or "",
                base_url=text["base_url"] or "",
                base_branch=text["base_branch"] or "",
                token=text["token"] or None,
                description=text["description"] or "",
                paths=paths,
            )
        except vcs.VcsError as exc:
            return _err(400, "INVALID_VCS", exc.message)
        return _connections()

    @router.post("/vcs/connections/{connection_id}/delete", response_model=None)
    def post_vcs_connection_delete(connection_id: str) -> dict | JSONResponse:
        try:
            removed = vcs.delete_connection(connection_id)
        except vcs.VcsError as exc:
            return _err(400, "INVALID_VCS", exc.message)
        if not removed:
            return _err(404, "NOT_FOUND", f"연결 {connection_id}가 없다")
        return _connections()

    @router.post("/vcs/connections/{connection_id}/test", response_model=None)
    async def post_vcs_connection_test(connection_id: str) -> dict | JSONResponse:
        """연결 하나 확인: 최근 하루 병합 목록을 읽어 본다 (쓰기 없음)."""
        from datetime import UTC, datetime, timedelta

        if not any(c.id == connection_id for c in vcs.connections()):
            return _err(404, "NOT_FOUND", f"연결 {connection_id}가 없다")
        now = datetime.now(UTC)
        result: dict[str, Any] = {"connection_id": connection_id, "checked_at": now.isoformat()}
        try:
            adapter = vcs.get_adapter(connection_id)
            changes = await adapter.find_changes(now - timedelta(days=1), now)
            result.update(ok=True, code=None, message=f"최근 하루 병합 {len(changes)}건 읽음")
        except vcs.VcsError as exc:
            result.update(ok=False, code=exc.code, message=exc.message)
        return _ok(result, get_service())

    @router.post("/reset")
    def post_reset() -> dict:
        svc = get_service()
        svc.reset()
        get_deploy_store().reset()
        return _ok(svc.snapshot(), svc)

    @router.get("/events")
    async def events(max_events: int | None = None) -> StreamingResponse:
        """`max_events`는 테스트·점검용: 그만큼 보내면 스트림을 닫는다."""
        svc = get_service()

        def frame() -> str:
            data = json.dumps(svc.snapshot(), ensure_ascii=False)
            return f"id: {svc.version}\nevent: state\ndata: {data}\n\n"

        async def stream():
            queue = svc.subscribe()
            sent = 0
            try:
                yield frame()
                sent += 1
                while max_events is None or sent < max_events:
                    try:
                        await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    while not queue.empty():  # 몰려온 변경은 마지막 상태 한 번으로
                        queue.get_nowait()
                    yield frame()
                    sent += 1
            finally:
                svc.unsubscribe(queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ---- 배포 기록·배포 후 확인 (deploy_store.py)

    def _deploy_row(dep_id: str, *, history: bool = False) -> dict:
        store = get_deploy_store()
        dep = store.get(dep_id)
        assert dep is not None
        check = store.latest_check(dep_id)
        row = {**dump(dep), "check": dump(check) if check else None}
        if history:
            row["checks"] = [dump(c) for c in store.checks.get(dep_id, [])]
        return row

    def _deploy_err(exc: DeployError) -> JSONResponse:
        status = {"NOT_FOUND": 404, "NOT_REQUESTED": 409, "NOT_DEPLOYED": 409}.get(exc.code, 400)
        return _err(status, exc.code, exc.message)

    def _gate_err(exc: GateRefused) -> JSONResponse:
        return _err(
            409,
            "GATE_REJECTED",
            f"실행 관문이 {exc.action}을 허락하지 않아 실행하지 않았다 ({exc.decision.status})",
        )

    async def _body(request: Request) -> dict | None:
        try:
            body = await request.json()
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    @router.get("/deployments")
    def list_deployments() -> dict:
        svc = get_service()
        rows = [_deploy_row(d.id) for d in reversed(get_deploy_store().deployments)]
        return _ok(rows, svc, count=len(rows))

    @router.get("/deployments/{dep_id}", response_model=None)
    def get_deployment(dep_id: str) -> dict | JSONResponse:
        svc = get_service()
        if get_deploy_store().get(dep_id) is None:
            return _err(404, "NOT_FOUND", f"배포 {dep_id}가 없다")
        return _ok(_deploy_row(dep_id, history=True), svc)

    @router.post("/deployments", response_model=None)
    async def post_deployment(request: Request) -> dict | JSONResponse:
        """배포 요청 `{pr_numbers: [28], env: "dev"|"prod"}`. prod·보호 경로면 `requested`."""
        svc = get_service()
        body = await _body(request)
        if body is None:
            return _err(400, "INVALID_DEPLOY", "본문은 JSON 객체여야 한다")
        nums = body.get("pr_numbers")
        env = body.get("env", "dev")
        if (
            not isinstance(nums, list)
            or not nums
            or not all(isinstance(n, int) and not isinstance(n, bool) for n in nums)
        ):
            return _err(400, "INVALID_DEPLOY", "pr_numbers는 PR 번호(정수) 목록이어야 한다")
        if env not in ("dev", "prod"):
            return _err(400, "INVALID_DEPLOY", "env는 dev 또는 prod")
        try:
            dep = await get_deploy_store().request(svc, nums, env)
        except DeployError as exc:
            return _deploy_err(exc)
        except GateRefused as exc:
            return _gate_err(exc)
        return _ok(_deploy_row(dep.id), svc, needs_approval=dep.status == "requested")

    @router.post("/deployments/{dep_id}/approve", response_model=None)
    async def post_approve_deployment(dep_id: str, request: Request) -> dict | JSONResponse:
        """사람 승인 버튼. 본문 `{approved_by}`는 없어도 된다 (목: 팀장)."""
        svc = get_service()
        body = await _body(request) or {}
        who = body.get("approved_by")
        who = who.strip() if isinstance(who, str) and who.strip() else TEAM_LEAD
        try:
            dep = await get_deploy_store().approve(svc, dep_id, who)
        except DeployError as exc:
            return _deploy_err(exc)
        except GateRefused as exc:
            return _gate_err(exc)
        return _ok(_deploy_row(dep.id), svc)

    @router.post("/deployments/{dep_id}/check", response_model=None)
    async def post_deployment_check(dep_id: str, request: Request) -> dict | JSONResponse:
        """배포 후 확인 (read). 본문 `{window_minutes}`는 없어도 된다 (기본 30분)."""
        svc = get_service()
        body = await _body(request) or {}
        window = body.get("window_minutes", 30)
        if isinstance(window, bool) or not isinstance(window, int) or not 1 <= window <= 1440:
            return _err(400, "INVALID_CHECK", "window_minutes는 1~1440 정수")
        try:
            await get_deploy_store().check(svc, dep_id, window)
        except DeployError as exc:
            return _deploy_err(exc)
        return _ok(_deploy_row(dep_id), svc)

    # ---- 코딩 에이전트: 개발팀원이 쓴 기획을 레포에 구현 (coder.py). 진행은 작업 `coding`(SSE)

    def _coding_err(exc: ActionError) -> JSONResponse:
        status = 409 if exc.code in CODING_CONFLICT else 404 if exc.code == "NOT_FOUND" else 400
        return _err(status, exc.code, exc.message)

    @router.post("/dev-tasks", response_model=None)
    async def post_dev_task(request: Request) -> dict | JSONResponse:
        """`{title, plan, done?, paths?, connection?}` → `{request_id, spec_id, task_id, status}`.

        진행은 작업 `coding`(SSE 상태)으로 본다."""
        svc = get_service()
        body = await _body(request)
        if body is None:
            return _err(400, "INVALID_INPUT", "본문은 JSON 객체여야 한다")
        title, plan, done = body.get("title"), body.get("plan"), body.get("done")
        paths, connection = body.get("paths"), body.get("connection")
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            return _err(400, "INVALID_INPUT", "title은 1~200자 문자열이어야 한다")
        if not isinstance(plan, str) or not plan.strip() or len(plan) > coder.MAX_PLAN_CHARS:
            return _err(
                400, "INVALID_INPUT", f"plan은 1~{coder.MAX_PLAN_CHARS}자 문자열이어야 한다"
            )
        if done is not None and (not isinstance(done, str) or len(done) > 4000):
            return _err(400, "INVALID_INPUT", "done은 4000자 이하 문자열이어야 한다")
        if paths is not None and (
            not isinstance(paths, list)
            or len(paths) > 20
            or not all(isinstance(p, str) and len(p) <= 300 for p in paths)
        ):
            return _err(400, "INVALID_INPUT", "paths는 문자열 목록(20개 이하)이어야 한다")
        if connection is not None and not isinstance(connection, str):
            return _err(400, "INVALID_INPUT", "connection은 문자열이어야 한다")
        try:
            result = await svc.create_dev_task(
                title=title, plan=plan, done=done, paths=paths, connection=connection or None
            )
        except ActionError as exc:
            return _coding_err(exc)
        except GateRefused as exc:
            return _gate_err(exc)
        return _ok(result.output, svc)

    @router.post("/dev-tasks/{task_id}/retry", response_model=None)
    async def post_dev_task_retry(task_id: str, request: Request) -> dict | JSONResponse:
        """실패·사람 확인으로 멈춘 코딩 작업 다시 돌리기. 본문 `{connection?}`은 없어도 된다."""
        svc = get_service()
        body = await _body(request) or {}
        connection = body.get("connection")
        if connection is not None and not isinstance(connection, str):
            return _err(400, "INVALID_INPUT", "connection은 문자열이어야 한다")
        try:
            result = await svc.retry_dev_task(task_id, connection or None)
        except ActionError as exc:
            return _coding_err(exc)
        except GateRefused as exc:
            return _gate_err(exc)
        return _ok(result.output, svc)

    # ---- 개발 AI 대화 (dev_chat.py). 계획이 서면 코딩 작업을 띄운다. 대화는 상태 `chats`(SSE)

    @router.get("/dev-chat")
    def list_dev_chats() -> dict:
        svc = get_service()
        rows = sorted(
            svc.state.chats,
            key=lambda c: (c.updated_at, int(c.id.split("-")[-1] or 0)),
            reverse=True,
        )[:MAX_CHATS_LISTED]
        return _list(rows, svc)

    @router.get("/dev-chat/{thread_id}", response_model=None)
    def get_dev_chat(thread_id: str) -> dict | JSONResponse:
        svc = get_service()
        th = svc.chat_of(thread_id)
        if th is None:
            return _err(404, "NOT_FOUND", f"대화 {thread_id}가 없다")
        return _ok(dump(th), svc)

    @router.post("/dev-chat", response_model=None)
    async def post_dev_chat(request: Request) -> dict | JSONResponse:
        """`{thread_id?, message, connection?}` → 바뀐 대화. AI 답 하나까지 기다린다."""
        svc = get_service()
        if dev_chat.get_llm() is None:
            return _err(409, "LLM_NOT_CONFIGURED", "LLM 설정이 없다 (PLANNING_LLM_*·LLM_*)")
        body = await _body(request)
        if body is None:
            return _err(400, "INVALID_INPUT", "본문은 JSON 객체여야 한다")
        thread_id, message, connection = (
            body.get("thread_id"),
            body.get("message"),
            body.get("connection"),
        )
        if thread_id is not None and (not isinstance(thread_id, str) or not thread_id):
            return _err(400, "INVALID_INPUT", "thread_id는 문자열이어야 한다")
        if (
            not isinstance(message, str)
            or not message.strip()
            or len(message) > dev_chat.MAX_MESSAGE_CHARS
        ):
            limit = dev_chat.MAX_MESSAGE_CHARS
            return _err(400, "INVALID_INPUT", f"message는 1~{limit}자 문자열이어야 한다")
        if connection is not None and not isinstance(connection, str):
            return _err(400, "INVALID_INPUT", "connection은 문자열이어야 한다")
        try:
            th = await dev_chat.turn(svc, thread_id, message, connection or None)
        except ActionError as exc:
            return _coding_err(exc)
        except GateRefused as exc:
            return _gate_err(exc)
        return _ok(dump(th), svc)

    return router
