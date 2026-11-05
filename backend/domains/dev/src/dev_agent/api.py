"""개발 서비스 화면 REST (`/api/planning/*`, dev 8111). 공용 라우트 + 형상관리 연결·배포 기록·
코딩 작업·개발 대화·저장소 작업 공간·구독형 작업 (분리 5단계: api.py에서 옮김)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ax_workflow.api import (
    ADMIN,
    CALLER,
    HUMAN,
    _body,
    _err,
    _gate_err,
    _list,
    _ok,
    add_common_routes,
)
from ax_workflow.auth import AuthError, check_human
from ax_workflow.base import TEAM_LEAD
from ax_workflow.core import ActionError, GateRefused
from ax_workflow.models import dump
from dev_agent import coder, dev_chat, llm_key, vcs
from dev_agent.deploy import DeployError
from dev_agent.deploy_store import get_deploy_store
from dev_agent.repo_api import add_repo_routes
from dev_agent.vcs import inventory
from dev_agent.work_api import add_work_routes

# 코딩 작업 오류 중 409로 주는 것 (설정·상태 문제). 나머지 입력 오류는 400
CODING_CONFLICT = {
    "THREAD_BUSY",  # 개발 AI 대화: 앞 말에 답하는 중
    "THREAD_CLOSED",  # 개발 AI 대화: 끝낸 대화
    "NO_VCS",
    "LLM_NOT_CONFIGURED",
    "NOT_RETRYABLE",
    "NOT_CANCELABLE",
    "NOT_REVISABLE",
    "WRONG_ROLE",
    "TRIAL_EXHAUSTED",  # 서버 AI 체험 한도를 다 씀
}
MAX_CHATS_LISTED = 20


def add_dev_routes(router: APIRouter, get_service: Callable[[], Any]) -> None:
    """개발 서비스만."""

    # ---- 개발 도구 연결: 형상관리 (시스템 관리자 화면)

    @router.get("/vcs")
    def get_vcs_config() -> dict:
        return _ok(vcs.describe(), get_service())

    # CORS가 GET·POST만 열려 있어서 저장도 POST로 받는다
    @router.post("/vcs", dependencies=ADMIN)
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

    @router.post("/vcs/test", dependencies=ADMIN)
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

    @router.post("/vcs/connections", response_model=None, dependencies=ADMIN)
    async def post_vcs_connection(request: Request) -> dict | JSONResponse:
        """연결 넣기·바꾸기. id가 없으면 새 연결(`conn-n`).

        토큰·App 개인 키를 비우면 이전 값을 둔다.

        `auth`: token(사람 토큰) · app(GitHub App: `app_id`·`installation_id`·`private_key`)."""
        body = await _body(request)
        if body is None:
            return _err(400, "INVALID_VCS", "본문은 JSON 객체여야 한다")
        paths = body.get("paths") or []
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            return _err(400, "INVALID_VCS", "paths는 문자열 목록이어야 한다")
        text = {
            k: body.get(k)
            for k in (
                "id",
                "kind",
                "repo",
                "base_url",
                "base_branch",
                "token",
                "description",
                "mount",
                "auth",
                "app_id",
                "installation_id",
                "private_key",
            )
        }
        if any(v is not None and not isinstance(v, str) for v in text.values()):
            return _err(400, "INVALID_VCS", "문자열 필드에 다른 값이 들어 있다")
        try:
            conn = vcs.upsert_connection(
                id=text["id"] or None,
                kind=text["kind"] or "",
                repo=text["repo"] or "",
                base_url=text["base_url"] or "",
                base_branch=text["base_branch"] or "",
                token=text["token"] or None,
                description=text["description"] or "",
                paths=paths,
                mount=text["mount"],
                auth=text["auth"] or None,
                app_id=text["app_id"],
                installation_id=text["installation_id"],
                private_key=text["private_key"] or None,
            )
        except vcs.VcsError as exc:
            return _err(400, "INVALID_VCS", exc.message)
        note = ""
        # 화면이 브랜치를 비우고 detect_branch를 켜면 레포 기본 브랜치를 읽어 넣는다
        if (
            conn.kind == "github"
            and body.get("detect_branch") is True
            and not (text["base_branch"] or "").strip()
        ):
            note = await _default_branch(conn)
        rows = vcs.list_connections()
        return _ok(rows, get_service(), count=len(rows), saved=conn.id, note=note)

    async def _default_branch(conn: vcs.Connection) -> str:
        """브랜치를 비우고 저장한 GitHub 연결: 레포 기본 브랜치를 읽어 넣는다 (토큰 확인도 겸한다).
        못 읽으면 dev로 두고 까닭을 돌려준다."""
        adapter = vcs.get_adapter(conn.id)
        read = getattr(adapter, "default_branch", None)
        if read is None:
            return ""
        try:
            branch = await read()
        except vcs.VcsError as exc:
            return f"레포를 못 읽음 ({exc.code}) · 기준 브랜치는 {conn.base_branch}"
        if branch and branch != conn.base_branch:
            vcs.upsert_connection(
                id=conn.id, kind=conn.kind, repo=conn.repo, base_url=conn.base_url,
                base_branch=branch, description=conn.description, paths=list(conn.paths),
                mount=conn.mount, auth=conn.auth, app_id=conn.app_id,
                installation_id=conn.installation_id,
            )  # fmt: skip
        return ""

    @router.post("/vcs/connections/{connection_id}/delete", response_model=None, dependencies=ADMIN)
    def post_vcs_connection_delete(connection_id: str) -> dict | JSONResponse:
        try:
            removed = vcs.delete_connection(connection_id)
        except vcs.VcsError as exc:
            return _err(400, "INVALID_VCS", exc.message)
        if not removed:
            return _err(404, "NOT_FOUND", f"연결 {connection_id}가 없다")
        inventory.forget(connection_id)
        return _connections()

    # ---- 연결 대장: 서버 종류·버전·토큰 권한·마지막 확인 (FR-AG-012 연결 시 보안 조건)

    @router.get("/vcs/inventory")
    def get_vcs_inventory() -> dict:
        rows = inventory.entries()
        return _ok(rows, get_service(), count=len(rows), policy=inventory.min_versions())

    @router.post("/vcs/inventory/check", response_model=None, dependencies=ADMIN)
    async def post_vcs_inventory_check() -> dict:
        """연결 전부를 지금 다시 읽는다 (쓰기 없음)."""
        await inventory.check_all()
        return get_vcs_inventory()

    @router.post("/vcs/connections/{connection_id}/test", response_model=None, dependencies=ADMIN)
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
        # 연결 대장도 같이 갱신한다 (서버 버전·토큰 권한). 화면은 저장 뒤 바로 이 확인을 부른다
        entry = await inventory.check(connection_id)
        result["inventory"] = asdict(entry) if entry else None
        return _ok(result, get_service())

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

    @router.post("/deployments", response_model=None, dependencies=CALLER)
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

    @router.post("/deployments/{dep_id}/approve", response_model=None, dependencies=HUMAN)
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

    @router.post("/deployments/{dep_id}/check", response_model=None, dependencies=HUMAN)
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
        status = (
            409
            if exc.code in CODING_CONFLICT
            else 404
            if exc.code == "NOT_FOUND"
            else 403
            if exc.code == "FORBIDDEN"
            else 400
        )
        return _err(status, exc.code, exc.message)

    @router.post("/dev-tasks", response_model=None, dependencies=CALLER)
    async def post_dev_task(request: Request) -> dict | JSONResponse:
        """`{title, plan, done?, paths?, connection?, mode?}`
        → `{request_id, spec_id, task_id, status}`.

        진행은 작업 `coding`(SSE 상태)으로 본다. `mode: "subscription"`이면 서버 에이전트 대신
        개발자 본인 Claude가 MCP로 가져갈 때까지 대기열에 둔다 (work_api.py)."""
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
        mode = body.get("mode", "agent")
        if mode not in ("agent", "subscription"):
            return _err(400, "INVALID_INPUT", "mode는 agent·subscription 중 하나다")
        try:
            result = await svc.create_dev_task(
                title=title,
                plan=plan,
                done=done,
                paths=paths,
                connection=connection or None,
                mode=mode,
            )
        except ActionError as exc:
            return _coding_err(exc)
        except GateRefused as exc:
            return _gate_err(exc)
        return _ok(result.output, svc)

    @router.post("/dev-tasks/{task_id}/retry", response_model=None, dependencies=HUMAN)
    async def post_dev_task_retry(task_id: str, request: Request) -> dict | JSONResponse:
        """실패·사람 확인으로 멈춘 코딩 작업 다시 돌리기. 본문 `{connection?}`은 없어도 된다."""
        svc = get_service()
        body = await _body(request) or {}
        connection = body.get("connection")
        if connection is not None and not isinstance(connection, str):
            return _err(400, "INVALID_INPUT", "connection은 문자열이어야 한다")
        approved = body.get("approved") is True  # 사람이 generate_pr 보류 건을 승인함
        try:
            result = await svc.retry_dev_task(task_id, connection or None, approved=approved)
        except ActionError as exc:
            return _coding_err(exc)
        except GateRefused as exc:
            return _gate_err(exc)
        return _ok(result.output, svc)

    @router.post("/dev-tasks/{task_id}/renew-hold", response_model=None, dependencies=HUMAN)
    async def post_dev_task_renew_hold(task_id: str) -> dict | JSONResponse:
        """PR 열기 보류 건 새로 받기 (만료·모름). 사람이 review_id를 승인한 뒤 retry(approved)."""
        svc = get_service()
        try:
            result = await svc.renew_hold(task_id)
        except ActionError as exc:
            return _coding_err(exc)
        except GateRefused as exc:
            return _gate_err(exc)
        return _ok(result.output, svc)

    @router.post("/dev-tasks/{task_id}/cancel", response_model=None, dependencies=HUMAN)
    async def post_dev_task_cancel(task_id: str) -> dict | JSONResponse:
        """코딩 작업 중단. 열린 PR은 닫고 브랜치를 지운다 (머지된 것은 되돌리지 않는다)."""
        svc = get_service()
        try:
            result = await svc.cancel_dev_task(task_id)
        except ActionError as exc:
            return _coding_err(exc)
        except GateRefused as exc:
            return _gate_err(exc)
        return _ok(result.output, svc)

    @router.post("/dev-tasks/{task_id}/to-agent", response_model=None, dependencies=HUMAN)
    async def post_dev_task_to_agent(task_id: str) -> dict | JSONResponse:
        """서버 AI로 넘기기: 개발자 Claude 대기열에서 아무도 안 가져간 작업을 서버 AI가 맡는다."""
        svc = get_service()
        try:
            result = await svc.hand_to_agent(task_id)
        except ActionError as exc:
            return _coding_err(exc)
        except GateRefused as exc:
            return _gate_err(exc)
        return _ok(result.output, svc)

    @router.post("/dev-tasks/{task_id}/text-ok", response_model=None)
    async def post_dev_task_text_ok(task_id: str, request: Request) -> dict | JSONResponse:
        """작업 글 경고를 사람이 확인함 → 내 Claude가 가져갈 수 있다 (사람 토큰만)."""
        svc = get_service()
        who = check_human(request.headers.get("authorization"))
        try:
            result = await svc.ok_task_text(task_id, who)
        except ActionError as exc:
            return _coding_err(exc)
        return _ok(result.output, svc)

    @router.post("/dev-tasks/{task_id}/close-covered", response_model=None, dependencies=HUMAN)
    async def post_dev_task_close_covered(task_id: str) -> dict | JSONResponse:
        """반영됨으로 닫기: 바뀐 내용이 없어 멈춘 작업을 앞 작업이 반영한 것으로 완료 (PR 없음)."""
        svc = get_service()
        try:
            result = await svc.close_dev_task_covered(task_id)
        except ActionError as exc:
            return _coding_err(exc)
        except GateRefused as exc:
            return _gate_err(exc)
        return _ok(result.output, svc)

    @router.post("/dev-tasks/{task_id}/revise", response_model=None, dependencies=HUMAN)
    async def post_dev_task_revise(task_id: str, request: Request) -> dict | JSONResponse:
        """사람 확인을 기다리는 코딩 PR에 수정 지시 `{note}`. 개발 AI가 고쳐 같은 PR에 커밋한다."""
        svc = get_service()
        body = await _body(request) or {}
        note = body.get("note")
        if not isinstance(note, str):
            return _err(400, "INVALID_INPUT", "note는 문자열이어야 한다")
        try:
            result = await svc.revise_dev_task(task_id, note)
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

    @router.get("/me", dependencies=CALLER)
    def get_me(request: Request) -> dict:
        """지금 화면을 쓰는 사람과 그 사람의 본인 Claude 연결 (사람 토큰일 때만 이름)."""
        svc = get_service()
        return _ok(svc.me(_person(request)), svc)

    @router.post("/dev-chat", response_model=None, dependencies=CALLER)
    async def post_dev_chat(request: Request) -> dict | JSONResponse:
        """`{thread_id?, message, connection?, mode?}` → `status: answering`인 대화를 바로 준다.
        답은 뒤에서 붙는다 (SSE `chats` 또는 GET으로 다시 읽는다).

        `mode`(agent·subscription)는 이 대화가 띄울 작업을 누가 코딩하나."""
        svc = get_service()
        if dev_chat.get_llm() is None:
            return _err(409, "LLM_NOT_CONFIGURED", "LLM 설정이 없다 (DEV_LLM_*·LLM_*)")
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
            mode = body.get("mode")
            who = _person(request)
            th = await dev_chat.send(svc, thread_id, message, connection or None, mode, who)
        except ActionError as exc:
            return _coding_err(exc)
        except GateRefused as exc:
            return _gate_err(exc)
        return _ok(dump(th), svc)

    @router.post("/dev-chat/{thread_id}/close", response_model=None, dependencies=CALLER)
    async def close_dev_chat(thread_id: str, request: Request) -> dict | JSONResponse:
        """대화를 끝낸다 (`closed`). 띄운 작업은 그대로 돈다."""
        svc = get_service()
        try:
            th = await dev_chat.close(svc, thread_id, _person(request))
        except ActionError as exc:
            return _coding_err(exc)
        return _ok(dump(th), svc)

    # ---- 레포 작업 공간 (repo_api.py): PR 게시판·상세·머지·되돌리기·브랜치
    add_repo_routes(router, get_service)

    # ---- 구독형 작업 (work_api.py): 개발자 본인 Claude가 MCP로 작업을 가져간다
    add_work_routes(router, get_service)


def add_settings_routes(router: APIRouter, get_service: Callable[[], Any]) -> None:
    """요청 작업 코딩 방식(회사 설정)·서버 AI 체험 한도."""

    @router.get("/coding-settings")
    def get_coding_settings() -> dict:
        svc = get_service()
        return _ok(svc.coding_settings(), svc)

    async def post_company_key(request: Request) -> dict | JSONResponse:
        """고객사 API 키(BYOK) 넣기·바꾸기 `{api_key?, base_url?, model?}` (관리자).

        키를 비우면 이전 키를 그대로 두고 주소·모델만 바꾼다. 응답에는 끝 4자리만 준다."""
        svc = get_service()
        body = await _body(request)
        if body is None:
            return _err(400, "INVALID_INPUT", "본문은 JSON 객체여야 한다")
        vals = {k: body.get(k) for k in ("api_key", "base_url", "model")}
        if any(v is not None and not isinstance(v, str) for v in vals.values()):
            return _err(400, "INVALID_INPUT", "문자열 필드에 다른 값이 들어 있다")
        try:
            llm_key.set_key(vals["api_key"], vals["base_url"] or "", vals["model"] or "")
        except llm_key.LlmKeyError as exc:
            return _err(400, "INVALID_INPUT", str(exc))
        return _ok(svc.coding_settings(), svc)

    async def post_company_key_delete() -> dict:
        """고객사 API 키 지우기 (관리자). 그 뒤로는 서버 기본 키와 체험 한도로 돈다."""
        svc = get_service()
        llm_key.clear()
        return _ok(svc.coding_settings(), svc)

    router.post("/coding-settings/company-key", response_model=None, dependencies=ADMIN)(
        post_company_key
    )
    router.post("/coding-settings/company-key/delete", dependencies=ADMIN)(post_company_key_delete)

    @router.post("/coding-settings", response_model=None, dependencies=ADMIN)
    async def post_coding_settings(request: Request) -> dict | JSONResponse:
        """`{mode?, models?: {plan, write, review}, budget_tokens?: int|null}` (관리자)."""
        svc = get_service()
        body = await _body(request)
        if body is None:
            return _err(400, "INVALID_INPUT", "본문은 JSON 객체여야 한다")
        models = body.get("models")
        if models is not None and not isinstance(models, dict):
            return _err(400, "INVALID_INPUT", "models는 {단계: 모델} 객체여야 한다")
        try:
            if models is not None:
                result = await svc.set_coding_models(models)
            if "budget_tokens" in body:
                result = await svc.set_budget(body.get("budget_tokens"))
            if "mode" in body or (models is None and "budget_tokens" not in body):
                result = await svc.set_coding_mode(str(body.get("mode", "")))
        except ActionError as exc:
            return _err(400, exc.code, exc.message)
        return _ok(result.output, svc)


def _person(request: Request) -> str | None:
    """사람 토큰이면 그 사람 이름 (에이전트 토큰·없음이면 None)."""
    try:
        return check_human(request.headers.get("authorization"))
    except AuthError:
        return None


def create_dev_router(get_service: Callable[[], Any]) -> APIRouter:
    """개발 서비스 화면 REST (공용 + 개발)."""
    router = APIRouter(prefix="/api/planning", tags=["dev"])
    add_common_routes(router, get_service)
    add_dev_routes(router, get_service)
    add_settings_routes(router, get_service)
    return router
