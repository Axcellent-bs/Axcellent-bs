"""화면용 REST (`/api/planning/*`). 성공 `{data, meta}`, 실패 `{error: {code, message}}`.

- `GET` 목록·상세는 상태를 읽기만 한다.
- `POST /actions`는 프로토타입 Action(`{"type": "confirmSpec", "spec": "SPEC-7"}`)을 그대로 받는다
  (`confirm_spec`처럼 snake_case도 받는다). 사람 버튼(확정·머지·승급 승인)은 여기로만 받는다.
- `GET /events`는 SSE(`text/event-stream`). 연결하면 현재 상태를 한 번 보내고, 상태가 바뀔 때마다
  `event: state` + 전체 상태 JSON을 보낸다. 15초마다 주석 줄로 연결을 유지한다.
- 쓰기(`POST`)는 `Authorization: Bearer <토큰>`이 있어야 한다 (auth.py).
  사람 결정은 사람 토큰, 초기화·형상관리 연결·자동 범위는 admin 토큰,
  요청 접수·대화는 사람이나 에이전트 토큰. 읽기·SSE는 열어 둔다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ax_workflow.api import ADMIN_ACTIONS, HUMAN, _err, _gate_err, _ok, add_common_routes  # noqa: F401  ADMIN_ACTIONS: 시험이 옛 경로로
from planning_agent.domain.projects import project_detail, project_summary, sorted_projects
from planning_agent.domain.service import (
    ActionError,
    GateRefused,
)


def add_planning_routes(router: APIRouter, get_service: Callable[[], Any]) -> None:
    """기획 서비스만: 프로젝트(요청 1건 깊게), 개선 초안 기각."""

    @router.get("/release-checks")
    def list_release_checks() -> dict:
        """배포 전 대조 기록 (최근 순). 서버 메모리라 다시 켜면 비어 있다."""
        svc = get_service()
        rows = list(reversed(list(svc.release_checks.values())))
        return _ok(rows, svc, count=len(rows))

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

    @router.post("/requests/{request_id}/reject", response_model=None, dependencies=HUMAN)
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
            return _gate_err(exc)
        return _ok(
            svc.snapshot(),
            svc,
            changed=result.changed,
            outcome=result.outcome,
            result=result.output,
        )


def create_api_router(get_service: Callable[[], Any]) -> APIRouter:
    """기획 서비스 화면 REST (공용 + 기획)."""
    router = APIRouter(prefix="/api/planning", tags=["planning"])
    add_common_routes(router, get_service)
    add_planning_routes(router, get_service)
    return router
