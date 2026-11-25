"""개발 서비스 화면 REST (`/api/planning/*`, dev 8111). 공용 라우트 + 형상관리 연결·배포 기록·
코딩 작업·개발 대화·저장소 작업 공간·구독형 작업 (분리 5단계: api.py에서 옮김)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ax_agent_template.config import is_mock
from ax_workflow.api import (
    ADMIN,
    CALLER,
    HUMAN,
    _body,
    _err,
    _gate_err,
    _ok,
    add_common_routes,
)
from ax_workflow.auth import (
    MOCK_NAME,
    AuthError,
    check_human,
    scoped_account,
    session_account,
    session_admin,
    session_decider,
    session_mode,
)
from ax_workflow.core import ActionError, GateRefused
from ax_workflow.models import dump
from dev_agent import coder, dev_chat, llm_key, scope, vcs, vcs_bridge
from dev_agent.deploy import DeployError, service_version
from dev_agent.deploy_store import get_deploy_store
from dev_agent.repo_api import add_repo_routes
from dev_agent.vcs import inventory
from dev_agent.work_api import add_work_routes

# 코딩 작업 오류 중 409로 주는 것 (설정·상태 문제). 나머지 입력 오류는 400
CODING_CONFLICT = {
    "NOT_DISCARDABLE",  # 폐기: 돌고 있거나 열린 PR이 있는 작업
    "NOT_RETURNABLE",  # 돌려보내기: 머지·완료·폐기했거나 이미 돌려보낸 작업
    "THREAD_BUSY",  # 개발 AI 대화: 앞 말에 답하는 중
    "THREAD_CLOSED",  # 개발 AI 대화: 끝낸 대화
    "NO_VCS",
    "LLM_NOT_CONFIGURED",
    "NOT_RETRYABLE",
    "NOT_CANCELABLE",
    "NOT_REVISABLE",
    "WRONG_ROLE",
    "TRIAL_EXHAUSTED",  # 서버 AI 체험 한도를 다 씀
    "NOT_MOVABLE",  # 서버 AI·내 Claude로 넘기기: 넘길 수 없는 상태
    "NOT_WAITING",  # 지금 진행: 순서를 기다리는 작업이 아님
}
# 승인 같은 사람 결정 (`POST /actions`). 세션 모드면 로그인 세션만 받고 그 계정을 결정한 사람으로
# 남긴다. 공용 사람 토큰은 누가 승인했는지 몰라 403 SESSION_REQUIRED (auth.session_decider)
DECISION_ACTIONS = frozenset({"merge_pr", "approve_repo", "reject_repo"})
MAX_CHATS_LISTED = 20
APPROVER_MAX = 40  # 화면 승인자 이름 칸(ApproverName.tsx)과 같은 길이


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

        `auth`: token(사람 토큰) · app(GitHub App). App은 보관함 `github_app` 연결 id(`app_ref`)로
        고른다. 예전 방식(`app_id`·`installation_id`·`private_key`를 직접)도 받는다."""
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
                "app_ref",
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
                app_ref=text["app_ref"],
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
                installation_id=conn.installation_id, app_ref=conn.app_ref,
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
        status = {
            "NOT_FOUND": 404,
            "NOT_REQUESTED": 409,
            "NOT_DEPLOYED": 409,
            "NOT_DISPATCHED": 409,
            "NO_RUN_ID": 409,
            "NOT_AVAILABLE": 503,
            "STATUS_UNAVAILABLE": 502,
            "ALREADY_SENT": 409,
            "NO_DEPLOY_TOOL": 409,
            "TOOLS_UNAVAILABLE": 503,
        }.get(exc.code, 400)
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
        """배포 요청 `{pr_numbers: [28], env: "dev"|"prod", version?: "shop-api v2.4.2"}`.

        prod·보호 경로면 `requested`, 아니면 `ready`. 어느 쪽도 저절로 보내지 않는다
        (배포 실행은 사람이 승인·'배포 실행'을 눌러야 한다).
        `version`이 없으면 PR의 배포 버전을 쓴다. `version`을 직접 주면 사람 토큰만 받고,
        그 서비스에 배포 도구가 연결돼 있어야 한다 (infra `deploy_tool_services`)."""
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
        version = body.get("version")
        if version is not None:
            # 배포 버전을 직접 정하는 것은 사람만 (AI가 임의 버전을 운영 배포 도구에 올리지 않게)
            check_human(request.headers.get("authorization"))
            pair = service_version(version) if isinstance(version, str) else None
            if pair is None:
                return _err(
                    400,
                    "INVALID_DEPLOY",
                    'version은 "서비스 버전" 형식, 각각 120자 이하 (예: shop-api v2.4.2)',
                )
            try:
                services = await get_deploy_store().tool_services(svc)
            except DeployError as exc:
                return _deploy_err(exc)
            if pair[0] not in services:
                return _err(
                    409,
                    "NO_DEPLOY_TOOL",
                    f"{pair[0]}에 배포 도구가 연결되지 않았어요 — 도구 연결에서 설정",
                )
            version = f"{pair[0]} {pair[1]}"
        try:
            dep = await get_deploy_store().request(svc, nums, env, version=version)
        except DeployError as exc:
            return _deploy_err(exc)
        except GateRefused as exc:
            return _gate_err(exc)
        return _ok(_deploy_row(dep.id), svc, needs_approval=dep.status in ("requested", "ready"))

    @router.post("/deployments/{dep_id}/approve", response_model=None, dependencies=HUMAN)
    async def post_approve_deployment(dep_id: str, request: Request) -> dict | JSONResponse:
        """사람 승인 버튼. 본문 `{approved_by}`(승인자 이름)가 꼭 있어야 한다.

        이름이 없으면 400. 기본 승인자를 채우지 않는다 (누가 승인했는지 지어내지 않는다).
        세션 모드면 로그인 세션만 받고(공용 토큰은 403 SESSION_REQUIRED) 승인자는 그 계정이다
        (본문 이름은 쓰지 않는다)."""
        session_who = session_decider(request.headers.get("authorization"))
        svc = get_service()
        if get_deploy_store().get(dep_id) is None:
            return _err(404, "NOT_FOUND", f"배포 {dep_id}가 없다")
        body = await _body(request) or {}
        who = session_who or body.get("approved_by")
        who = who.strip()[:APPROVER_MAX] if isinstance(who, str) else ""
        if not who:
            return _err(400, "APPROVER_REQUIRED", "approved_by(승인자 이름)가 필요하다")
        try:
            dep = await get_deploy_store().approve(svc, dep_id, who)
        except DeployError as exc:
            return _deploy_err(exc)
        except GateRefused as exc:
            return _gate_err(exc)
        return _ok(_deploy_row(dep.id), svc)

    @router.post("/deployments/{dep_id}/dispatch", response_model=None, dependencies=HUMAN)
    async def post_dispatch_deployment(dep_id: str, request: Request) -> dict | JSONResponse:
        """'배포 실행' 버튼 (사람만). 승인 대기(`requested`·`ready`)면 그 사람의 승인으로 기록하고,
        승인했지만 보내지 않은 배포(`not_sent`)면 한 번 보낸다. 이미 보냈으면 409 `ALREADY_SENT`.
        사람 이름 규칙은 승인 버튼과 같다 (세션 모드면 로그인 세션 계정만)."""
        session_who = session_decider(request.headers.get("authorization"))
        svc = get_service()
        if get_deploy_store().get(dep_id) is None:
            return _err(404, "NOT_FOUND", f"배포 {dep_id}가 없다")
        body = await _body(request) or {}
        who = session_who or body.get("approved_by")
        who = who.strip()[:APPROVER_MAX] if isinstance(who, str) else ""
        if not who:
            return _err(400, "APPROVER_REQUIRED", "approved_by(누른 사람 이름)가 필요하다")
        try:
            dep = await get_deploy_store().send(svc, dep_id, who)
        except DeployError as exc:
            return _deploy_err(exc)
        except GateRefused as exc:
            return _gate_err(exc)
        return _ok(_deploy_row(dep.id), svc)

    @router.post("/deployments/{dep_id}/refresh", response_model=None, dependencies=HUMAN)
    async def post_deployment_refresh(dep_id: str) -> dict | JSONResponse:
        """배포 도구 실행 상태 다시 읽기 (infra deploy_run_status, read). 도구에서 끝났으면
        `running` → `succeeded`·`failed`."""
        svc = get_service()
        try:
            await get_deploy_store().refresh(svc, dep_id)
        except DeployError as exc:
            return _deploy_err(exc)
        return _ok(_deploy_row(dep_id), svc)

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

    @router.post("/demo/incident-merge", response_model=None, dependencies=ADMIN)
    async def post_demo_incident_merge(request: Request) -> dict | JSONResponse:
        """시연 준비(운영자): 장애 직전 배포로 나간 머지 PR을 지금 시각으로 기록한다.

        본문 `{deployed_version: "shop-api v2.4.2", title?, changed_paths?}`. infra 시연 배포와
        같이 `scripts/demo_deploy.sh`가 부른다. 롤백 교차검증이 이 PR을 찾는다.

        코딩 작업·형상관리를 거치지 않은 지어낸 머지 기록이라 목 모드(시험 전용)에서만 받는다.
        실제 모드는 403 `SIMULATOR_DISABLED` (화면에 시연 데이터를 넣지 않는다)."""
        if not is_mock(MOCK_NAME):
            return _err(403, "SIMULATOR_DISABLED", "시연용 머지 기록은 목 모드에서만 받는다")
        svc = get_service()
        body = await _body(request)
        if body is None:
            return _err(400, "INVALID_DEMO", "본문은 JSON 객체여야 한다")
        version = body.get("deployed_version")
        title = body.get("title") or "결제 타임아웃 재조정 (시연 배포)"
        paths = body.get("changed_paths", ["shop-api/payment/config.py"])
        if not isinstance(version, str) or len(version.split()) != 2:
            return _err(400, "INVALID_DEMO", 'deployed_version은 "서비스 버전" 형식이어야 한다')
        if not isinstance(title, str) or not (
            isinstance(paths, list) and paths and all(isinstance(p, str) and p for p in paths)
        ):
            return _err(400, "INVALID_DEMO", "title은 글자, changed_paths는 경로 목록이어야 한다")
        pr = await svc.record_demo_merge(deployed_version=version, title=title, changed_paths=paths)
        return _ok(dump(pr), svc)

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

    def _not_owner(request: Request, task_id: str, account: str | None) -> JSONResponse | None:
        """작업 API 소유 확인. 남이 낸 요청(대화)에서 온 작업이면 403 VIEW_ONLY, 아니면 None.

        화면이 보기만 하는 대화의 작업 버튼을 숨겨도 진짜 방어는 여기다 (dev_chat.may_operate).
        세션 모드면 세션 토큰이 있어야 하고 계정은 세션 계정이다(`?account=`가 다르면 403).
        세션 설정이 꺼진 데모 모드에서만 `?account=` 값을 쓴다 (auth.scoped_account)."""
        if account is not None and not 0 < len(account) <= 64:
            return _err(400, "INVALID_INPUT", "account는 1~64자 문자열이어야 한다")
        auth = request.headers.get("authorization")
        mine = scoped_account(auth, account)
        admin = session_admin(auth)
        if dev_chat.may_operate(get_service(), task_id, mine, _person(request), admin):
            return None
        return _err(403, "VIEW_ONLY", dev_chat.TASK_OWNER_ONLY)

    def _account_of(request: Request, given: object) -> str | None | JSONResponse:
        """대화 REST의 계정: 형식을 보고 세션 계정과 맞춘다 (`scoped_account`)."""
        if given is not None and (not isinstance(given, str) or not 0 < len(given) <= 64):
            return _err(400, "INVALID_INPUT", "account는 1~64자 문자열이어야 한다")
        return scoped_account(request.headers.get("authorization"), given)

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
                assignee=_assignee(request) if mode == "subscription" else None,
            )
        except ActionError as exc:
            return _coding_err(exc)
        except GateRefused as exc:
            return _gate_err(exc)
        return _ok(result.output, svc)

    @router.post("/dev-tasks/{task_id}/retry", response_model=None, dependencies=HUMAN)
    async def post_dev_task_retry(
        task_id: str, request: Request, account: str | None = None
    ) -> dict | JSONResponse:
        """실패·사람 확인으로 멈춘 코딩 작업 다시 돌리기. 본문 `{connection?, paths?}`는 없어도
        된다. paths는 사람이 직접 고른 고칠 파일 (AI가 파일을 고르지 않는다)."""
        if (refused := _not_owner(request, task_id, account)) is not None:
            return refused
        svc = get_service()
        body = await _body(request) or {}
        connection = body.get("connection")
        if connection is not None and not isinstance(connection, str):
            return _err(400, "INVALID_INPUT", "connection은 문자열이어야 한다")
        approved = body.get("approved") is True  # 사람이 generate_pr 보류 건을 승인함
        paths = body.get("paths")  # 사람이 직접 고른 고칠 파일
        if paths is not None and (
            not isinstance(paths, list) or not all(isinstance(x, str) for x in paths)
        ):
            return _err(400, "INVALID_INPUT", "paths는 문자열 목록이어야 한다")
        try:
            result = await svc.retry_dev_task(
                task_id, connection or None, approved=approved, paths=paths or None
            )
        except ActionError as exc:
            return _coding_err(exc)
        except GateRefused as exc:
            return _gate_err(exc)
        return _ok(result.output, svc)

    @router.post("/dev-tasks/{task_id}/renew-hold", response_model=None, dependencies=HUMAN)
    async def post_dev_task_renew_hold(
        task_id: str, request: Request, account: str | None = None
    ) -> dict | JSONResponse:
        """PR 열기 보류 건 새로 받기 (만료·모름). 사람이 review_id를 승인한 뒤 retry(approved)."""
        if (refused := _not_owner(request, task_id, account)) is not None:
            return refused
        svc = get_service()
        try:
            result = await svc.renew_hold(task_id)
        except ActionError as exc:
            return _coding_err(exc)
        except GateRefused as exc:
            return _gate_err(exc)
        return _ok(result.output, svc)

    @router.get("/holds", dependencies=CALLER)
    def get_holds() -> dict:
        """관문이 보류한 개발 AI 액션과 사람 결정 뒤 이어서 한 결과 (hold_resume, 읽기)."""
        svc = get_service()
        rows = svc.holds.listed()
        return _ok(rows, svc, count=len(rows))

    @router.post("/dev-tasks/{task_id}/cancel", response_model=None, dependencies=HUMAN)
    async def post_dev_task_cancel(
        task_id: str, request: Request, account: str | None = None
    ) -> dict | JSONResponse:
        """코딩 작업 중단. 열린 PR은 닫고 브랜치를 지운다 (머지된 것은 되돌리지 않는다)."""
        if (refused := _not_owner(request, task_id, account)) is not None:
            return refused
        svc = get_service()
        try:
            result = await svc.cancel_dev_task(task_id)
        except ActionError as exc:
            return _coding_err(exc)
        except GateRefused as exc:
            return _gate_err(exc)
        return _ok(result.output, svc)

    @router.post("/dev-tasks/{task_id}/return", response_model=None, dependencies=HUMAN)
    async def post_dev_task_return(
        task_id: str, request: Request, account: str | None = None
    ) -> dict | JSONResponse:
        """사람이 기획에 돌려보내기 `{to, reason}`. 돌고 있으면 멈추고 열린 PR은 닫는다."""
        if (refused := _not_owner(request, task_id, account)) is not None:
            return refused
        svc = get_service()
        body = await _body(request)
        if body is None:
            return _err(400, "INVALID_INPUT", "본문은 JSON 객체여야 한다")
        to, reason = body.get("to"), body.get("reason")
        if not isinstance(to, str) or not isinstance(reason, str):
            return _err(400, "INVALID_INPUT", "to·reason은 문자열이어야 한다")
        try:
            result = await svc.return_dev_task(task_id, to, reason, _person(request))
        except ActionError as exc:
            return _coding_err(exc)
        except GateRefused as exc:
            return _gate_err(exc)
        return _ok(result.output, svc)

    @router.post("/dev-tasks/{task_id}/discard", response_model=None, dependencies=HUMAN)
    async def post_dev_task_discard(
        task_id: str, request: Request, account: str | None = None
    ) -> dict | JSONResponse:
        """멈춘 작업 폐기 (중단·실패·돌려보냄, 열린 PR 없음). 다시 돌리지 않는다."""
        if (refused := _not_owner(request, task_id, account)) is not None:
            return refused
        svc = get_service()
        try:
            result = await svc.discard_dev_task(task_id, _person(request))
        except ActionError as exc:
            return _coding_err(exc)
        except GateRefused as exc:
            return _gate_err(exc)
        return _ok(result.output, svc)

    @router.post("/dev-tasks/{task_id}/to-agent", response_model=None, dependencies=HUMAN)
    async def post_dev_task_to_agent(
        task_id: str, request: Request, account: str | None = None
    ) -> dict | JSONResponse:
        """서버 AI로 넘기기: 개발자 Claude 대기열에서 아무도 안 가져간 작업을 서버 AI가 맡는다."""
        if (refused := _not_owner(request, task_id, account)) is not None:
            return refused
        svc = get_service()
        try:
            result = await svc.hand_to_agent(task_id)
        except ActionError as exc:
            return _coding_err(exc)
        except GateRefused as exc:
            return _gate_err(exc)
        return _ok(result.output, svc)

    @router.post("/dev-tasks/{task_id}/to-subscription", response_model=None, dependencies=HUMAN)
    async def post_dev_task_to_subscription(
        task_id: str, request: Request, account: str | None = None
    ) -> dict | JSONResponse:
        """내 Claude로 넘기기: 멈춘 서버 AI 작업(열린 PR 없음)을 개발자 Claude 대기열에 올린다."""
        if (refused := _not_owner(request, task_id, account)) is not None:
            return refused
        svc = get_service()
        try:
            result = await svc.hand_to_subscription(
                task_id, assignee=_hand_assignee(svc, request, task_id)
            )
        except ActionError as exc:
            return _coding_err(exc)
        except GateRefused as exc:
            return _gate_err(exc)
        return _ok(result.output, svc)

    @router.post("/dev-tasks/{task_id}/skip-queue", response_model=None, dependencies=HUMAN)
    async def post_dev_task_skip_queue(
        task_id: str, request: Request, account: str | None = None
    ) -> dict | JSONResponse:
        """지금 진행: 앞 작업 순서를 기다리는 작업을 기다리지 않고 잇게 한다 (사람이 누를 때만)."""
        if (refused := _not_owner(request, task_id, account)) is not None:
            return refused
        svc = get_service()
        try:
            result = await svc.skip_queue_dev_task(task_id, _person(request))
        except ActionError as exc:
            return _coding_err(exc)
        return _ok(result.output, svc)

    @router.post("/dev-tasks/{task_id}/text-ok", response_model=None)
    async def post_dev_task_text_ok(
        task_id: str, request: Request, account: str | None = None
    ) -> dict | JSONResponse:
        """작업 글 경고를 사람이 확인함 → 내 Claude가 가져갈 수 있다 (사람 토큰만)."""
        svc = get_service()
        who = check_human(request.headers.get("authorization"))
        if (refused := _not_owner(request, task_id, account)) is not None:
            return refused
        try:
            result = await svc.ok_task_text(task_id, who)
        except ActionError as exc:
            return _coding_err(exc)
        return _ok(result.output, svc)

    @router.post("/dev-tasks/{task_id}/close-covered", response_model=None, dependencies=HUMAN)
    async def post_dev_task_close_covered(
        task_id: str, request: Request, account: str | None = None
    ) -> dict | JSONResponse:
        """반영됨으로 닫기: 바뀐 내용이 없어 멈춘 작업을 앞 작업이 반영한 것으로 완료 (PR 없음)."""
        if (refused := _not_owner(request, task_id, account)) is not None:
            return refused
        svc = get_service()
        try:
            result = await svc.close_dev_task_covered(task_id)
        except ActionError as exc:
            return _coding_err(exc)
        except GateRefused as exc:
            return _gate_err(exc)
        return _ok(result.output, svc)

    @router.post("/dev-tasks/{task_id}/revise", response_model=None, dependencies=HUMAN)
    async def post_dev_task_revise(
        task_id: str, request: Request, account: str | None = None
    ) -> dict | JSONResponse:
        """사람 확인을 기다리는 코딩 PR에 수정 지시 `{note}`. 개발 AI가 고쳐 같은 PR에 커밋한다."""
        if (refused := _not_owner(request, task_id, account)) is not None:
            return refused
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

    @router.get("/dev-chat", response_model=None)
    def list_dev_chats(request: Request, account: str | None = None) -> dict | JSONResponse:
        """`?account=` 개발 화면 계정이 연 대화만 준다. 세션 토큰이면 그 계정 (다르면 403)."""
        svc = get_service()
        account = _account_of(request, account)
        if isinstance(account, JSONResponse):
            return account
        rows = sorted(
            [
                c
                for c in svc.live_chats()
                if dev_chat.seen_from(c, SCREEN) and dev_chat.of_account(c, account)
            ],
            key=lambda c: (c.updated_at, int(c.id.split("-")[-1] or 0)),
            reverse=True,
        )[:MAX_CHATS_LISTED]
        data = [dev_chat.rest_view(svc, c) for c in rows]
        return _ok(data, svc, count=len(data))

    @router.get("/dev-chat/{thread_id}", response_model=None)
    def get_dev_chat(
        thread_id: str, request: Request, account: str | None = None
    ) -> dict | JSONResponse:
        svc = get_service()
        account = _account_of(request, account)
        if isinstance(account, JSONResponse):
            return account
        th = svc.chat_of(thread_id)
        if th is None or not dev_chat.seen_from(th, SCREEN) or not dev_chat.of_account(th, account):
            return _err(404, "NOT_FOUND", f"대화 {thread_id}가 없다")
        return _ok(dev_chat.rest_view(svc, th), svc)

    @router.get("/me", dependencies=CALLER)
    def get_me(request: Request) -> dict:
        """지금 화면을 쓰는 사람과 그 사람의 본인 Claude 연결 (사람 토큰일 때만 이름)."""
        svc = get_service()
        return _ok(svc.me(_person(request)), svc)

    @router.post("/dev-chat", response_model=None, dependencies=CALLER)
    async def post_dev_chat(request: Request) -> dict | JSONResponse:
        """`{thread_id?, message, connection?, mode?, pr?}` → `status: answering`인 대화를 바로
        준다. 답은 뒤에서 붙는다 (SSE `chats` 또는 GET으로 다시 읽는다).

        `mode`(agent·subscription)는 이 대화가 띄울 작업을 누가 코딩하나.
        `pr`(`{number, connection?}`)은 지금 보는 PR (형상관리 번호). 그 PR CI를 읽어 답한다."""
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
        pr = body.get("pr")
        if pr is not None and (
            not isinstance(pr, dict)
            or not isinstance(pr.get("number"), int)
            or isinstance(pr.get("number"), bool)
            or not isinstance(pr.get("connection") or "", str)
        ):
            return _err(400, "INVALID_INPUT", "pr은 {number, connection?} 객체여야 한다")
        account = _account_of(request, body.get("account"))
        if isinstance(account, JSONResponse):
            return account
        try:
            mode = body.get("mode")
            who = _person(request)
            th = await dev_chat.send(
                svc, thread_id, message, connection or None, mode, who, SCREEN, account, pr
            )
        except ActionError as exc:
            return _coding_err(exc)
        except GateRefused as exc:
            return _gate_err(exc)
        return _ok(dev_chat.rest_view(svc, th), svc)

    @router.get("/dev-tasks/{task_id}/files", response_model=None)
    async def get_dev_task_files(task_id: str, q: str = "") -> dict | JSONResponse:
        """'파일 직접 고르기' 후보: 이 작업 연결 레포의 파일 경로 (최대 50개, 읽기만, LLM 없음).

        `q`가 있으면 경로에 그 글이 든 것만, 작업 글·담당 경로에 맞는 순서로 준다."""
        svc = get_service()
        task = next((t for t in svc.state.tasks if t.id == task_id), None)
        if task is None or task.coding is None:
            return _err(404, "NOT_FOUND", f"코딩 작업 {task_id}가 없다")
        job = svc.coding_job(task_id, 0)
        try:
            adapter = vcs_bridge.active(task.coding.connection)
            paths = await adapter.list_files(limit=coder.ALL_FILES) if adapter else []
        except vcs.VcsError as exc:
            return _err(502, "VCS_ERROR", f"레포 파일 목록을 읽지 못했다 ({exc.code})")
        needle = q.strip().lower()[:100]
        if needle:
            paths = [p for p in paths if needle in p.lower()]
        text = f"{q} {coder.job_text(job)}" if job else q
        rows = coder.listing_for(paths, job.hints if job else (), text)[:MAX_PICK_LIST]
        return _ok({"files": rows, "existing": len(paths)}, svc)

    @router.post("/dev-tasks/{task_id}/to-chat", response_model=None, dependencies=HUMAN)
    async def post_dev_task_to_chat(task_id: str, request: Request) -> dict | JSONResponse:
        """멈춘 작업을 개발 AI 대화로 넘긴다 (작업과 이어진 새 대화). 본문 `{account?}`.

        `answering`인 대화를 바로 주고, AI가 뒤에서 멈춘 이유를 보고 원인·고칠 파일을 답한다.
        '진행해'면 같은 작업을 그 파일로 다시 시도한다."""
        svc = get_service()
        body = await _body(request) or {}
        account = _account_of(request, body.get("account"))
        if isinstance(account, JSONResponse):
            return account
        if (refused := _not_owner(request, task_id, account)) is not None:
            return refused
        try:
            th = await dev_chat.open_for_task(svc, task_id, _person(request), account, SCREEN)
        except ActionError as exc:
            return _coding_err(exc)
        return _ok(dev_chat.rest_view(svc, th), svc)

    @router.post("/dev-chat/{thread_id}/close", response_model=None, dependencies=CALLER)
    async def close_dev_chat(
        thread_id: str, request: Request, account: str | None = None
    ) -> dict | JSONResponse:
        """대화를 끝낸다 (`closed`). 띄운 작업은 그대로 돈다. `?account=` 연 계정."""
        svc = get_service()
        account = _account_of(request, account)
        if isinstance(account, JSONResponse):
            return account
        try:
            th = await dev_chat.close(svc, thread_id, _person(request), SCREEN, account)
        except ActionError as exc:
            return _coding_err(exc)
        return _ok(dev_chat.rest_view(svc, th), svc)

    @router.post("/dev-chat/{thread_id}/remove", response_model=None, dependencies=CALLER)
    async def remove_dev_chat(
        thread_id: str, request: Request, account: str | None = None
    ) -> dict | JSONResponse:
        """대화를 숨긴다 (기록은 남긴다, A2A `dev_chat_remove`와 같은 함수). 띄운 작업은 그대로
        돈다. 답하는 중이어도 숨긴다 (늦게 온 답은 버려진다). `?account=` 연 계정."""
        svc = get_service()
        account = _account_of(request, account)
        if isinstance(account, JSONResponse):
            return account
        try:
            tid = await dev_chat.remove(svc, thread_id, _person(request), SCREEN, account)
        except ActionError as exc:
            return _coding_err(exc)
        return _ok({"thread_id": tid, "removed": True}, svc)

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
        if not llm_key.legacy_writes_allowed():
            return _err(
                409,
                "MOVED_TO_COMPANY_AI",
                "코딩 키는 시스템 관리 › 회사 연결 › 공통 AI 도구 연결에서 넣어요",
            )
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


# 개발 화면 대화 패널에서 연 대화. 대시보드 AI 대화방(dashboard) 대화와 서로 보이지 않는다
MAX_PICK_LIST = 50  # 파일 직접 고르기 후보 수
SCREEN = "screen"


def _person(request: Request) -> str | None:
    """사람 토큰이면 그 사람 이름 (에이전트 토큰·없음이면 None)."""
    try:
        return check_human(request.headers.get("authorization"))
    except AuthError:
        return None


def _assignee(request: Request) -> str | None:
    """세션 모드에서 내 Claude 작업을 맡길 사람: 누른 계정 (`acct:<계정 ID>`, work_auth 이름).

    그 계정의 개인 MCP 연결(내 Claude)만 가져간다 (work_queue). 세션 모드가 아니거나 세션 토큰이
    아니면 None (예전처럼 맡긴 사람 없음)."""
    if not session_mode():
        return None
    account = session_account(request.headers.get("authorization"))
    return f"acct:{account}" if account else None


def _hand_assignee(svc: Any, request: Request, task_id: str) -> str | None:
    """내 Claude로 넘길 때 맡길 사람. 작업 주인(대화를 연 계정)이 따로 있고 누른 사람이 그
    주인이 아니면(관리자가 남의 작업을 넘김) 주인 계정에게 맡긴다. 나머지는 누른 계정
    (`_assignee`)."""
    mine = _assignee(request)
    if mine is None:
        return None
    rid = dev_chat.task_request(svc.state, task_id)
    chats = dev_chat.request_owners(svc.state, rid) if rid else []
    owners = [c.account for c in chats if c.account]
    if owners and mine.removeprefix("acct:") not in owners:
        return f"acct:{owners[0]}"
    return mine


def create_dev_router(get_service: Callable[[], Any]) -> APIRouter:
    """개발 서비스 화면 REST (공용 + 개발)."""
    router = APIRouter(prefix="/api/planning", tags=["dev"])

    def guard(name: str, body: dict, authorization: str | None) -> JSONResponse | None:
        """세션 모드: 작업·PR을 건드리는 액션은 그 작업 주인(대화를 연 계정)과 관리자만 (#1556)."""
        if name not in scope.TASK_ACTIONS or not session_mode():
            return None
        scope.check_session(authorization)  # 누구인지부터 (401·403)
        svc = get_service()
        try:
            tasks = scope.tasks_of_action(svc, name, body)
        except scope.BadAction as exc:
            return _err(400, "INVALID_ACTION", str(exc))
        if not tasks:  # 모르는 PR·기획서: 주인을 가를 수 없어 통과시키지 않는다 (fail-closed)
            return _err(404, "NOT_FOUND", "작업·PR을 찾지 못했다")
        if scope.may_operate_tasks(svc, tasks, authorization):
            return None
        return _err(403, "VIEW_ONLY", dev_chat.TASK_OWNER_ONLY)

    add_common_routes(router, get_service, session_actions=DECISION_ACTIONS, action_guard=guard)
    add_dev_routes(router, get_service)
    add_settings_routes(router, get_service)
    return router
