"""도구 연결 API: 시스템 관리 > 도구 연결에서 GitHub 앱·배포 도구·DejaFix 연결을 정한다.

- `GET    /api/dashboard/tool-connections`: 연결 목록(`public()`, 비밀 값 없음)과
  종류별 입력 칸 정의(`FIELDS`)
- `POST   /api/dashboard/tool-connections/test`: 저장하지 않고 연결 확인
  (`id`가 있으면 빈 비밀은 저장된 값으로)
- `POST   /api/dashboard/tool-connections`, `PUT .../{id}`: 다시 확인하고 모두 통과하면 저장
  (실패하면 422 `CHECK_FAILED`, 항목별 결과는 `error.checks`)
- `POST   /api/dashboard/tool-connections/{id}/check`: 저장된 연결을 다시 확인하고 결과를 남긴다
- `DELETE /api/dashboard/tool-connections/{id}`: 지우기. 가리키는 연결이 있으면 409 `IN_USE`

쓰기는 사람 토큰만 된다(`require_human_for_writes`). 주소 칸은 `check_host`(사내 주소·허용 목록)를
통과해야 한다.
보관함 파일이 깨졌거나 경로가 없어 못 쓰면 409 `STORE_UNAVAILABLE`(문구에 이유와 조치).
비밀 값은 응답·로그·오류 문구에 넣지 않는다.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ax_agent_template import company_connection as host_policy
from ax_agent_template import tool_connections as store
from ax_agent_template.tool_connections import ToolConnection, ToolConnectionError
from ax_dashboard_api.auth import require_human_for_writes
from ax_dashboard_api.company_connection import Check
from ax_dashboard_api.envelope import ApiError, ok
from ax_dashboard_api.tool_checks import run_checks

logger = logging.getLogger("ax.dashboard.tool_connections")

router = APIRouter(
    prefix="/api/dashboard/tool-connections",
    dependencies=[Depends(require_human_for_writes)],
)


def _f(
    key: str, label: str, *, secret: bool = False, required: bool = True, help: str = ""
) -> dict:  # noqa: A002
    return {"key": key, "label": label, "secret": secret, "required": required, "help": help}


FIELDS: dict[str, list[dict[str, Any]]] = {
    "github_app": [
        _f("app_id", "앱 ID", help="GitHub 앱 설정 맨 위의 App ID (숫자)"),
        _f("installation_id", "설치 ID", help="앱을 조직에 설치한 주소 끝의 숫자"),
        _f("private_key", "개인 키(.pem)", secret=True, help="앱 설정에서 만든 개인 키 파일 내용"),
        _f("base_url", "GitHub Enterprise 주소", required=False, help="비워 두면 api.github.com"),
    ],
    "github_actions": [
        _f("app", "GitHub 앱 연결", help="위에서 연결한 GitHub 앱"),
        _f("repo", "레포", help="owner/name"),
        _f("workflow", "배포 워크플로 파일", help="예: deploy.yml"),
        _f("version_input", "버전 입력 이름", required=False, help="비워 두면 version"),
        _f("service", "대상 서비스", help="이 도구가 배포하는 서비스 이름"),
    ],
    "jenkins": [
        _f("url", "Jenkins 주소", help="예: https://jenkins.example.com"),
        _f("username", "사용자 이름"),
        _f("api_token", "API 토큰", secret=True),
        _f("job", "작업 이름", help="폴더 안 작업은 폴더/작업"),
        _f("version_param", "버전 매개변수 이름", required=False, help="비워 두면 version"),
        _f("service", "대상 서비스", help="이 도구가 배포하는 서비스 이름"),
    ],
    "dejafix": [
        _f("url", "DejaFix 백엔드 주소"),
        _f("username", "viewer 계정 이름"),
        _f("password", "비밀번호", secret=True),
    ],
}
URL_KEYS = {"github_app": ("base_url",), "jenkins": ("url",), "dejafix": ("url",)}
DEPLOY_KINDS = ("github_actions", "jenkins")


class ToolConnectionIn(BaseModel):
    kind: str = Field(min_length=1, max_length=40)
    name: str = Field(default="", max_length=100)
    config: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)
    id: str | None = Field(default=None, max_length=100)


def _human(request: Request) -> str:
    return getattr(request.state, "human", "unknown")


def _store_error(exc: ToolConnectionError) -> ApiError:
    """보관함 오류를 응답 오류로. 파일을 못 읽거나 쓸 수 없으면 409, 입력 문제는 422."""
    if exc.code == "not_found":
        return ApiError(404, "NOT_FOUND", exc.message)
    if exc.code == "invalid":
        return ApiError(422, "INVALID_REQUEST", exc.message)
    return ApiError(409, "STORE_UNAVAILABLE", exc.message)


def _clean(body: ToolConnectionIn) -> tuple[dict[str, Any], dict[str, str]]:
    """칸 정의에 있는 값만 남기고 주소를 검사한다. 비밀 칸이 config로 새지 않게 나눈다."""
    fields = FIELDS.get(body.kind)
    if fields is None:
        raise ApiError(422, "INVALID_REQUEST", f"모르는 연결 종류예요: {body.kind}")
    config: dict[str, Any] = {}
    for f in fields:
        if f["secret"]:
            continue
        raw = body.config.get(f["key"])
        value = raw.strip() if isinstance(raw, str) else ""
        if value:
            config[f["key"]] = value
    for key in URL_KEYS.get(body.kind, ()):
        if key in config:
            try:
                config[key] = host_policy.validate_shop_url(config[key])
                host_policy.check_host(config[key])
            except ValueError as exc:
                raise ApiError(422, "INVALID_URL", str(exc)) from exc
    secrets = {f["key"]: body.secrets.get(f["key"], "") for f in fields if f["secret"]}
    return config, secrets


def _require_fields(kind: str, config: dict[str, Any], secrets: dict[str, str]) -> None:
    missing = [
        f["label"]
        for f in FIELDS[kind]
        if f["required"] and not (secrets.get(f["key"]) if f["secret"] else config.get(f["key"]))
    ]
    if missing:
        raise ApiError(422, "MISSING_FIELD", f"입력해 주세요: {', '.join(missing)}")


def _saved(conn_id: str | None) -> ToolConnection | None:
    if not conn_id:
        return None
    found = store.get(conn_id)
    if found is None:
        raise ApiError(404, "NOT_FOUND", "없는 연결이에요")
    return found


def _guard_unique(kind: str, config: dict[str, Any], own_id: str | None) -> None:
    others = [c for c in store.read_all() if c.id != own_id]
    if kind in DEPLOY_KINDS:
        service = config.get("service")
        taken = next(
            (c for c in others if c.kind in DEPLOY_KINDS and c.config.get("service") == service),
            None,
        )
        if taken is not None:
            raise ApiError(
                409,
                "SERVICE_TAKEN",
                f"{service} 서비스에는 이미 배포 도구({taken.name})가 연결돼 있어요. "
                "먼저 끊어 주세요",
            )
    elif kind == "dejafix" and any(c.kind == "dejafix" for c in others):
        raise ApiError(409, "ALREADY_CONNECTED", "DejaFix 연결은 하나만 둘 수 있어요")


def _summary(checks: list[Check]) -> str:
    shown = [c for c in checks if not c.ok] or checks
    return "; ".join(f"{c.label}: {c.detail}" for c in shown)


async def _run(kind: str, config: dict[str, Any], secrets: dict[str, str]) -> list[Check]:
    return await run_checks(kind, config, secrets, lookup=store.get)


def _merged(saved: ToolConnection | None, secrets: dict[str, str]) -> dict[str, str]:
    """입력한 비밀 값 위에 빈 칸은 저장된 값을 채운다."""
    merged = dict(saved.secrets) if saved else {}
    merged.update({k: v for k, v in secrets.items() if v})
    return merged


def _failed(checks: list[Check]) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "CHECK_FAILED",
                "message": f"연결 확인에 실패했어요 ({_summary(checks)})",
                "checks": [c.as_dict() for c in checks],
            }
        },
    )


@router.get("")
def list_connections() -> dict:
    return ok({"connections": [c.public() for c in store.read_all()], "fields": FIELDS})


@router.post("/test")
async def test_connection(body: ToolConnectionIn) -> dict:
    saved = _saved(body.id)
    if saved is not None and saved.kind != body.kind:
        raise ApiError(422, "INVALID_REQUEST", "연결 종류는 바꿀 수 없어요")
    config, secrets = _clean(body)
    checks = await _run(body.kind, config, _merged(saved, secrets))
    return ok({"ok": all(c.ok for c in checks), "checks": [c.as_dict() for c in checks]})


async def _save(body: ToolConnectionIn, request: Request, conn_id: str | None):  # noqa: ANN202
    saved = _saved(conn_id)
    config, secrets = _clean(body)
    merged = _merged(saved, secrets)
    _require_fields(body.kind, config, merged)
    _guard_unique(body.kind, config, conn_id)
    checks = await _run(body.kind, config, merged)
    if not all(c.ok for c in checks):
        return _failed(checks)
    try:
        conn = store.upsert(
            body.kind, body.name, config, secrets, conn_id=conn_id, by=_human(request)
        )
        conn = store.record_check(conn.id, True, _summary(checks))
    except ToolConnectionError as exc:
        raise _store_error(exc) from exc
    logger.info("도구 연결 저장: %s (%s, by %s)", conn.id, conn.kind, _human(request))
    return ok({"connection": conn.public(), "checks": [c.as_dict() for c in checks]})


@router.post("")
async def create_connection(body: ToolConnectionIn, request: Request):  # noqa: ANN201
    return await _save(body.model_copy(update={"id": None}), request, None)


@router.put("/{conn_id}")
async def update_connection(conn_id: str, body: ToolConnectionIn, request: Request):  # noqa: ANN201
    return await _save(body, request, conn_id)


@router.post("/{conn_id}/check")
async def check_connection(conn_id: str) -> dict:
    conn = _saved(conn_id)
    assert conn is not None  # conn_id가 있으면 _saved는 값이 없을 때 404를 낸다
    checks = await _run(conn.kind, conn.config, conn.secrets)
    passed = all(c.ok for c in checks)
    try:
        conn = store.record_check(conn.id, passed, _summary(checks))
    except ToolConnectionError as exc:
        raise _store_error(exc) from exc
    return ok({"connection": conn.public(), "ok": passed, "checks": [c.as_dict() for c in checks]})


@router.delete("/{conn_id}")
def delete_connection(conn_id: str, request: Request) -> dict:
    conn = _saved(conn_id)
    assert conn is not None
    users = [
        c for c in store.read_all() if c.kind == "github_actions" and c.config.get("app") == conn_id
    ]
    if users:
        names = ", ".join(c.name for c in users)
        raise ApiError(
            409, "IN_USE", f"{names} 연결이 이 연결을 쓰고 있어요. 먼저 그 연결을 끊어 주세요"
        )
    try:
        store.delete(conn_id, by=_human(request))
    except ToolConnectionError as exc:
        raise _store_error(exc) from exc
    return ok({"deleted": conn_id})
