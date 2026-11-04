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
from fastapi.responses import JSONResponse, RedirectResponse

from ax_workflow.api import (  # noqa: F401  ADMIN_ACTIONS: 시험이 옛 경로로
    ADMIN,
    ADMIN_ACTIONS,
    HUMAN,
    _body,
    _err,
    _gate_err,
    _ok,
    add_common_routes,
)
from planning_agent import notion_import, notion_oauth, notion_store, spec_fill
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


def add_notion_routes(router: APIRouter, get_service: Callable[[], Any]) -> None:
    """연결 페이지 › 문서 › 노션: 노션 토큰 넣기·확인·지우기 (관리자). 응답에는 끝 4자리만."""

    @router.get("/notion/connection")
    def get_notion_connection() -> dict:
        return _ok(notion_store.public(), get_service())

    @router.post("/notion/connection", response_model=None, dependencies=ADMIN)
    async def post_notion_connection(request: Request) -> dict | JSONResponse:
        """`{token}`을 노션 `users/me`로 확인한 뒤에만 저장한다 (틀린 토큰은 저장하지 않는다)."""
        body = await _body(request)
        if body is None or not isinstance(body.get("token"), str):
            return _err(400, "INVALID_INPUT", "본문은 {token: 문자열}이어야 한다")
        try:
            key = notion_store.validate(body["token"])
            workspace = await notion_import.check(key)
        except notion_store.NotionTokenError as exc:
            return _err(400, "INVALID_INPUT", str(exc))
        except notion_import.NotionError as exc:
            return _err(400, exc.code, exc.message)
        notion_store.set_token(key, workspace)
        return _ok(notion_store.public(), get_service())

    @router.post("/notion/connection/check", response_model=None, dependencies=ADMIN)
    async def post_notion_check() -> dict | JSONResponse:
        """지금 쓰는 토큰(저장한 것 또는 서버 환경변수)으로 노션 연결을 다시 확인한다."""
        key = notion_import.token()
        if not key:
            return _err(400, "NOTION_NOT_CONFIGURED", "노션 토큰이 없습니다")
        try:
            workspace = await notion_import.check(key)
        except notion_import.NotionError as exc:
            return _err(400, exc.code, exc.message)
        notion_store.mark_checked(workspace)
        return _ok({**notion_store.public(), "workspace": workspace}, get_service())

    @router.post("/notion/search", response_model=None, dependencies=HUMAN)
    async def post_notion_search(request: Request) -> dict | JSONResponse:
        """`{query}` 제목으로 노션 페이지 찾기 (가져오기 전 고르기). 사람 토큰만 (노션 내용이라)."""
        body = await _body(request)
        query = body.get("query") if body else None
        if not isinstance(query, str) or not query.strip():
            return _err(400, "INVALID_INPUT", "본문은 {query: 문자열}이어야 한다")
        try:
            hits = await notion_import.search(query)
        except notion_import.NotionError as exc:
            return _err(400, exc.code, exc.message)
        rows = [{"id": h.id, "title": h.title, "url": h.url, "edited": h.edited} for h in hits]
        return _ok(rows, get_service())

    @router.post("/notion/oauth/start", response_model=None, dependencies=ADMIN)
    def post_notion_oauth_start() -> dict | JSONResponse:
        """노션 계정 연결 시작: 화면이 이 주소로 이동한다 (노션 로그인 → 페이지 고르기)."""
        try:
            return _ok({"url": notion_oauth.start_url()}, get_service())
        except notion_import.NotionError as exc:
            return _err(400, exc.code, exc.message)

    @router.get("/notion/oauth/callback", response_model=None)
    async def get_notion_oauth_callback(
        code: str = "", state: str = "", error: str = ""
    ) -> RedirectResponse:
        """노션이 돌려보내는 곳. 결과와 함께 화면 연결 페이지로 보낸다 (토큰은 주소에 넣지 않는다).
        돌려보낼 곳은 서버 설정 값만 쓴다 (주소의 값으로 정하지 않는다 → 열린 리다이렉트 없음)."""

        def back(result: str, reason: str = "") -> RedirectResponse:
            url = notion_oauth.web_url(result, notion_oauth.safe_code(reason))
            res = RedirectResponse(url, status_code=302)
            res.headers["Cache-Control"] = "no-store"
            return res

        if error:
            notion_oauth.take_state(state)  # 거절해도 state는 버린다
            return back("denied")
        if not code or len(code) > 500 or not notion_oauth.take_state(state):
            return back("bad_state")
        try:
            token, workspace = await notion_oauth.exchange(code)
            notion_store.set_token(token, workspace)
        except notion_import.NotionError as exc:
            return back("failed", exc.detail)
        except notion_store.NotionTokenError:
            return back("failed", "bad_token")
        return back("ok")

    @router.post("/notion/connection/delete", dependencies=ADMIN)
    def post_notion_delete() -> dict:
        """화면에서 넣은 토큰을 지운다 (서버 환경변수 토큰은 그대로)."""
        notion_store.clear()
        return _ok(notion_store.public(), get_service())


def add_spec_fill_route(router: APIRouter, get_service: Callable[[], Any]) -> None:
    """자동 채우기 (화면 "수정" 중): 기획 AI가 자유 작성을 읽은 값만 돌려준다. 저장하지 않는다.

    기획서를 서버에서 찾지 않으므로 예시 모드 화면에서도 같은 AI가 읽는다.
    기획 LLM이 없으면 소제목 읽기 결과와 `by: "rule"`을 돌려준다 (화면이 그대로 알린다)."""

    @router.post("/spec-fill", response_model=None, dependencies=HUMAN)
    async def post_spec_fill(request: Request) -> dict | JSONResponse:
        body = await _body(request)
        md = body.get("body_md") if body else None
        if not isinstance(md, str):
            return _err(400, "SPEC_NO_FREE_TEXT", "자유 작성이 비어 있어요")
        if not md.strip() and body.get("mode") != "suggest":  # 제안은 제목·근거만으로도 낸다
            return _err(400, "SPEC_NO_FREE_TEXT", "자유 작성이 비어 있어요")
        cur = body.get("current") if isinstance(body.get("current"), dict) else {}
        current = {
            "problem": str(cur.get("problem") or "")[:500],
            "goal": str(cur.get("goal") or "")[:500],
            "requirements": [str(x)[:300] for x in (cur.get("requirements") or [])][:10],
            "done_criteria": [str(x)[:300] for x in (cur.get("done_criteria") or [])][:10],
        }
        svc = get_service()
        tpl = "proposal" if body.get("template") == "proposal" else "plan"
        filler = getattr(svc, "_spec_filler", None)
        if body.get("mode") == "suggest":  # 비어 있는 칸에 쓸 안 (추측 포함, 화면은 "AI 제안"으로)
            fields = [str(f)[:40] for f in (body.get("fields") or [])][:12]
            basis = [str(b)[:200] for b in (body.get("basis") or [])][:20]
            sug = await spec_fill.suggest(
                md[:20000], title=str(body.get("title") or "")[:200], current=current,
                basis=basis, fields=fields, filler=filler, template=tpl,
                context_id=f"spec-suggest:{str(body.get('spec') or '-')[:40]}",
            )  # fmt: skip
            out = {
                "problem": sug.problem, "goal": sug.goal, "reqs": sug.reqs, "done": sug.done,
                "answer": "", "extra": sug.extra, "by": sug.by, "ai": filler is not None,
            }  # fmt: skip
            return _ok(out, svc)
        got = await spec_fill.fill(
            md[:20000],
            title=str(body.get("title") or "")[:200],
            current=current,
            question=str(body.get("question") or "")[:500],
            context_id=f"spec-fill:{str(body.get('spec') or '-')[:40]}",
            filler=getattr(svc, "_spec_filler", None),
            template="proposal" if body.get("template") == "proposal" else "plan",
        )
        out = {
            "problem": got.problem, "goal": got.goal, "reqs": got.reqs, "done": got.done,
            "answer": got.answer, "extra": got.extra, "by": got.by,
            "ai": getattr(svc, "_spec_filler", None) is not None,
        }  # fmt: skip
        return _ok(out, svc)


def create_api_router(get_service: Callable[[], Any]) -> APIRouter:
    """기획 서비스 화면 REST (공용 + 기획)."""
    router = APIRouter(prefix="/api/planning", tags=["planning"])
    add_common_routes(router, get_service)
    add_planning_routes(router, get_service)
    add_notion_routes(router, get_service)
    add_spec_fill_route(router, get_service)
    return router
