"""화면용 REST(`/api/planning/*`) 중 기획·개발 서비스가 같이 쓰는 것 (분리 5단계).

성공 `{data, meta}`, 실패 `{error: {code, message}}`.

- `GET` 목록·상세는 상태를 읽기만 한다.
- `POST /actions`는 프로토타입 Action(`{"type": "confirmSpec", "spec": "SPEC-7"}`)을 그대로 받는다
  (`confirm_spec`처럼 snake_case도 받는다). 서비스마다 받는 액션 표가 다르다.
- `GET /events`는 SSE(`text/event-stream`). 연결하면 현재 상태를 한 번 보내고, 상태가 바뀔 때마다
  `event: state` + 전체 상태 JSON을 보낸다. 15초마다 주석 줄로 연결을 유지한다.
- 쓰기(`POST`)는 `Authorization: Bearer <토큰>`이 있어야 한다 (auth.py).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ax_workflow.auth import (
    check_admin,
    check_caller,
    check_human,
    require_admin,
    require_caller,
    require_human,
)
from ax_workflow.base import (
    SIMULATOR_ACTIONS,
    ActionError,
    GateRefused,
    PlanningCore,
    action_name,
)
from ax_workflow.metrics import compute_metrics
from ax_workflow.models import dump
from ax_workflow.rules import AUTO_LEVELS, PROMOTE_AFTER, auto_limit
from ax_workflow.trace import code_of, trace_of, trace_text

KEEPALIVE_SECONDS = 15.0

# 쓰기 토큰 종류 (auth.py). 라우트마다 `dependencies=`로 건다
HUMAN = [Depends(require_human)]
ADMIN = [Depends(require_admin)]
CALLER = [Depends(require_caller)]
# `POST /actions`는 액션 종류로 고른다: 자동 범위 바꾸기는 admin, 시뮬레이터 접수는 사람·에이전트,
# 나머지(확정·머지·레포 승인·기각 등)는 사람
ADMIN_ACTIONS = frozenset({"approve_promotion", "reject_promotion"})
INTAKE_ACTIONS = frozenset(SIMULATOR_ACTIONS)


def _check_action(body: Any, authorization: str | None) -> None:
    raw = body.get("type") if isinstance(body, dict) else None
    name = action_name(raw) if isinstance(raw, str) else ""
    if name in ADMIN_ACTIONS:
        check_admin(authorization)
    elif name in INTAKE_ACTIONS:
        check_caller(authorization)
    else:
        check_human(authorization)


def _ok(data: Any, svc: PlanningCore, **meta: Any) -> dict:
    return {"data": data, "meta": {"tenant_id": svc.tenant_id, "version": svc.version, **meta}}


def _err(status: int, code: str, message: str, details: dict | None = None) -> JSONResponse:
    error: dict = {"code": code, "message": message}
    if details:
        error["details"] = details
    return JSONResponse(status_code=status, content={"error": error})


def _list(items: list, svc: PlanningCore) -> dict:
    data = [dump(x) for x in items]
    return _ok(data, svc, count=len(data))


def _gate_err(exc: GateRefused) -> JSONResponse:
    return _err(
        409,
        "GATE_REJECTED",
        f"실행 관문이 {exc.action}을 허락하지 않아 실행하지 않았다 ({exc.decision.status})",
        # 보류(held)면 사람 승인 대기 번호. 화면이 사람 토큰으로 safety에 승인하고 다시 보낸다
        {
            "action": exc.action,
            "status": exc.decision.status,
            "review_id": exc.decision.review_id,
        },
    )


async def _body(request: Request) -> dict | None:
    try:
        body = await request.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def add_common_routes(router: APIRouter, get_service: Callable[[], Any]) -> None:
    """두 서비스가 같이 쓰는 읽기·액션·SSE·초기화."""

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
            # 닫힌 PR(작업 중단·레포에서 닫음)은 더 판단할 게 없어 대기열에서 뺀다
            if not pr.merged and not pr.closed and pr.auto_review is not None:
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
    async def post_action(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict | JSONResponse:
        svc = get_service()
        try:
            body = await request.json()
        except ValueError:
            check_human(authorization)
            return _err(400, "INVALID_ACTION", "JSON이 아니다")
        _check_action(body, authorization)
        try:
            result = await svc.dispatch(body)
        except ActionError as exc:
            status = 403 if exc.code == "SIMULATOR_DISABLED" else 400
            return _err(status, exc.code, exc.message)
        except GateRefused as exc:
            return _gate_err(exc)
        return _ok(
            svc.snapshot(),
            svc,
            changed=result.changed,
            outcome=result.outcome,
            result=result.output,
        )

    @router.post("/reset", dependencies=ADMIN)
    def post_reset() -> dict:
        svc = get_service()
        svc.reset()
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
                        # 화면이 열려 있는 동안 오래된 교차검증 대기를 사람 확인으로 넘긴다
                        await svc.tick()
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
