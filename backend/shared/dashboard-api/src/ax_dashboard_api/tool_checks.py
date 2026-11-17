"""도구 연결 종류별 확인: 저장하기 전·저장된 연결을 다시 볼 때, 그 도구를 실제로 불러 본다.

- `github_app`: 설치 토큰 받기 → 설치된 레포 수 → 받은 권한 목록
- `github_actions`: 앱 연결의 `actions: write` 권한 → 워크플로 파일
  → `workflow_dispatch` 입력 이름 → `run-name`에 버전 입력이 들어가는가 → 최근 실행
- `jenkins`: 작업 조회(기본 인증) → 매개변수 정의에 버전 이름이 있는가 → 최근 빌드
- `dejafix`: 로그인 → 현재 장애 목록 읽기

모든 호출은 5초 제한이다. 항목마다 통과 여부와 "무엇을 고치면 되는지"를 사람 말로 적는다.
오류 문구에는 httpx 예외 문구·응답 본문을 넣지 않고(예외 종류만),
입력·저장된 비밀 값은 `••••`로 바꾼 뒤 낸다.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx
import yaml

from ax_agent_template import company_connection as host_policy
from ax_agent_template.github_app import GithubAppAuth
from ax_agent_template.tool_connections import ToolConnection, ToolConnectionError
from ax_dashboard_api.company_connection import Check

TIMEOUT_SECONDS = 5.0
# 테스트에서 httpx.MockTransport로 바꿔 끼운다
TRANSPORT: httpx.AsyncBaseTransport | None = None

GITHUB_API = "https://api.github.com"
GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
REPO_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")

Lookup = Callable[[str], ToolConnection | None]


class _Scrub:
    """오류 문구에서 비밀 값을 `••••`로 바꾼다."""

    def __init__(self, *values: str) -> None:
        self._values: set[str] = set()
        self.add(*values)

    def add(self, *values: str) -> None:
        self._values.update(v for v in values if v)

    def __call__(self, text: str) -> str:
        for value in sorted(self._values, key=len, reverse=True):
            text = text.replace(value, "••••")
        return text


def _s(config: dict[str, Any], key: str, default: str = "") -> str:
    value = config.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else default


def _client(**kwargs: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=TIMEOUT_SECONDS, follow_redirects=False, transport=TRANSPORT, **kwargs
    )


async def _send(
    client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
) -> tuple[httpx.Response | None, str | None]:
    """응답 또는 실패 이유. 예외 문구는 쓰지 않고 종류만 적는다."""
    try:
        return await client.request(method, url, **kwargs), None
    except httpx.TimeoutException:
        return None, f"{TIMEOUT_SECONDS:g}초 안에 답이 없음"
    except httpx.HTTPError as exc:
        return None, f"연결할 수 없음 ({type(exc).__name__})"


def _json(response: httpx.Response) -> Any | None:
    try:
        return response.json()
    except ValueError:
        return None


def _github_http_problem(status: int, what: str) -> str:
    if status == 401:
        return f"{what}: 인증에 실패했어요 (HTTP 401). GitHub 앱 연결을 확인해 주세요"
    if status == 403:
        return f"{what}: 권한이 없어요 (HTTP 403). 앱 설치 권한과 레포 선택을 확인해 주세요"
    if status == 404:
        return f"{what}: 찾을 수 없어요 (HTTP 404). 이름과 앱이 설치된 레포인지 확인해 주세요"
    return f"{what}: HTTP {status}"


def _auth_from(conn: ToolConnection) -> tuple[GithubAppAuth, str]:
    auth = GithubAppAuth(
        _s(conn.config, "app_id"),
        _s(conn.config, "installation_id"),
        conn.secrets.get("private_key", ""),
    )
    return auth, _s(conn.config, "base_url", GITHUB_API).rstrip("/")


def _host_problem(url: str) -> str | None:
    """나가기 직전 주소 정책 확인 (저장된 연결도 같다). 어긋나면 이유."""
    try:
        host_policy.check_host(host_policy.validate_shop_url(url))
    except ValueError as exc:
        return f"주소를 쓸 수 없어요 (INVALID_URL): {exc}"
    return None


async def _github_app_checks(
    conn: ToolConnection, client: httpx.AsyncClient, scrub: _Scrub
) -> tuple[list[Check], GithubAppAuth | None, str]:
    """설치 토큰 → 레포 수 → 권한. 토큰을 못 받으면 거기서 멈춘다."""
    try:
        auth, base = _auth_from(conn)
    except ToolConnectionError as exc:
        return [Check("token", "설치 토큰", False, scrub(exc.message))], None, GITHUB_API
    if base != GITHUB_API:
        problem = _host_problem(base)
        if problem:
            return [Check("token", "설치 토큰", False, problem)], None, base
    checks: list[Check] = []
    try:
        token = await auth.token(client, base)
    except ToolConnectionError as exc:
        scrub.add(*auth.secrets())
        return [Check("token", "설치 토큰", False, scrub(exc.message))], None, base
    scrub.add(token)
    checks.append(Check("token", "설치 토큰", True, "설치 토큰을 받았어요"))
    headers = {**GITHUB_HEADERS, "Authorization": f"Bearer {token}"}
    res, error = await _send(
        client, "GET", f"{base}/installation/repositories", params={"per_page": 1}, headers=headers
    )
    if error or res is None:
        checks.append(Check("repos", "설치된 레포", False, error or ""))
    elif res.status_code != 200:
        checks.append(
            Check("repos", "설치된 레포", False, _github_http_problem(res.status_code, "레포 목록"))
        )
    else:
        body = _json(res)
        total = body.get("total_count") if isinstance(body, dict) else None
        if isinstance(total, int):
            checks.append(Check("repos", "설치된 레포", True, f"레포 {total:,}개에 설치돼 있어요"))
        else:
            checks.append(Check("repos", "설치된 레포", False, "응답에 total_count가 없어요"))
    perms = ", ".join(f"{k}: {v}" for k, v in sorted(auth.permissions.items()))
    checks.append(
        Check("permissions", "앱 권한", bool(perms), perms or "받은 권한 목록이 비어 있어요")
    )
    return checks, auth, base


def _workflow_path(workflow: str) -> str:
    path = workflow.strip().lstrip("/")
    return path if "/" in path else f".github/workflows/{path}"


def _dispatch_problem(text: str, version_input: str) -> str | None:
    """워크플로 YAML에서 `workflow_dispatch` 입력 이름을 찾는다. 문제가 없으면 None."""
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        return "워크플로 파일을 YAML로 읽지 못했어요. 문법을 확인해 주세요"
    if not isinstance(doc, dict):
        return "워크플로 파일이 올바른 YAML이 아니에요"
    # PyYAML은 키 `on`을 True로 읽는다
    triggers = doc.get("on", doc.get(True))
    fix = f"on: 아래에 workflow_dispatch와 inputs.{version_input}을 추가해 주세요"
    if isinstance(triggers, dict):
        if "workflow_dispatch" not in triggers:
            return f"워크플로에 workflow_dispatch가 없어요. {fix}"
        dispatch = triggers["workflow_dispatch"]
    elif isinstance(triggers, list):
        if "workflow_dispatch" not in triggers:
            return f"워크플로에 workflow_dispatch가 없어요. {fix}"
        dispatch = None
    elif triggers == "workflow_dispatch":
        dispatch = None
    else:
        return f"워크플로에 workflow_dispatch가 없어요. {fix}"
    inputs = dispatch.get("inputs") if isinstance(dispatch, dict) else None
    if not isinstance(inputs, dict) or version_input not in inputs:
        return (
            f"워크플로에 {version_input} 입력이 없어요. "
            f"workflow_dispatch 입력에 {version_input}을 추가해 주세요"
        )
    return None


def _run_name_problem(text: str, version_input: str) -> str | None:
    """`run-name`이 버전 입력을 담는지 본다. 없으면 실행 제목에서 버전을 못 읽어 되돌릴 수 없다.

    `${{ inputs.X }}`, `${{ github.event.inputs.X }}` 꼴(괄호 안 공백은 있어도 없어도)을 받는다.
    """
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        return "워크플로 파일을 YAML로 읽지 못했어요. 문법을 확인해 주세요"
    run_name = doc.get("run-name") if isinstance(doc, dict) else None
    pattern = (
        r"\$\{\{[^}]*?(?<![\w.])(?:github\.event\.)?inputs\."
        + re.escape(version_input)
        + r"(?![\w-])[^}]*\}\}"
    )
    if isinstance(run_name, str) and re.search(pattern, run_name):
        return None
    return (
        f'워크플로에 run-name: "deploy ${{{{ inputs.{version_input} }}}}"처럼 버전을 넣어 주세요. '
        "그래야 되돌릴 버전을 알 수 있어요"
    )


async def _github_actions_checks(
    config: dict[str, Any], client: httpx.AsyncClient, lookup: Lookup, scrub: _Scrub
) -> list[Check]:
    app = lookup(_s(config, "app"))
    if app is None or app.kind != "github_app":
        return [
            Check(
                "app",
                "GitHub 앱 연결",
                False,
                "연결된 GitHub 앱을 찾을 수 없어요. 다시 골라 주세요",
            )
        ]
    scrub.add(*app.secret_values())
    app_checks, auth, base = await _github_app_checks(app, client, scrub)
    token_check = app_checks[0]
    if auth is None:
        return [
            Check(
                "app", "GitHub 앱 연결", False, f"GitHub 앱을 쓸 수 없어요 ({token_check.detail})"
            )
        ]
    checks = [Check("app", "GitHub 앱 연결", True, f"{app.name} 앱으로 토큰을 받았어요")]
    scrub.add(*auth.secrets())
    if auth.permissions.get("actions") == "write":
        checks.append(Check("permission", "Actions 권한", True, "actions: write 권한이 있어요"))
    else:
        have = auth.permissions.get("actions")
        why = f"지금은 actions: {have}예요" if have else "지금은 actions 권한이 없어요"
        checks.append(
            Check(
                "permission",
                "Actions 권한",
                False,
                f"앱에 actions: write 권한이 필요해요 ({why}). "
                "앱 설정에서 Actions를 Read and write로 바꿔 주세요",
            )
        )
    repo, workflow = _s(config, "repo"), _s(config, "workflow")
    version_input = _s(config, "version_input", "version")
    if not REPO_PATTERN.fullmatch(repo):
        checks.append(
            Check("workflow", "워크플로 파일", False, "레포는 owner/name 모양이어야 해요")
        )
        return checks
    if not workflow:
        checks.append(
            Check("workflow", "워크플로 파일", False, "워크플로 파일 이름을 입력해 주세요")
        )
        return checks
    headers = {**GITHUB_HEADERS, "Authorization": f"Bearer {auth.secrets()[0]}"}
    path = _workflow_path(workflow)
    res, error = await _send(
        client, "GET", f"{base}/repos/{repo}/contents/{quote(path)}", headers=headers
    )
    if error or res is None:
        checks.append(Check("workflow", "워크플로 파일", False, error or ""))
        return checks
    if res.status_code != 200:
        problem = _github_http_problem(res.status_code, f"{path}")
        checks.append(Check("workflow", "워크플로 파일", False, problem))
        return checks
    body = _json(res)
    try:
        raw = body["content"] if isinstance(body, dict) else None
        text = base64.b64decode(str(raw)).decode("utf-8")
    except (KeyError, TypeError, ValueError, binascii.Error):
        checks.append(Check("workflow", "워크플로 파일", False, "파일 내용을 읽지 못했어요"))
        return checks
    checks.append(Check("workflow", "워크플로 파일", True, f"{path}를 읽었어요"))
    problem = _dispatch_problem(text, version_input)
    if problem:
        checks.append(Check("input", "버전 입력", False, problem))
    else:
        checks.append(
            Check("input", "버전 입력", True, f"workflow_dispatch에 {version_input} 입력이 있어요")
        )
    problem = _run_name_problem(text, version_input)
    if problem:
        checks.append(Check("run_name", "실행 이름의 버전", False, problem))
    else:
        detail = f"run-name에 inputs.{version_input}이 있어요"
        checks.append(Check("run_name", "실행 이름의 버전", True, detail))
    file_name = quote(path.rsplit("/", 1)[-1])
    res, error = await _send(
        client,
        "GET",
        f"{base}/repos/{repo}/actions/workflows/{file_name}/runs",
        params={"per_page": 1},
        headers=headers,
    )
    if error or res is None:
        checks.append(Check("runs", "최근 실행", False, error or ""))
    elif res.status_code != 200:
        checks.append(
            Check("runs", "최근 실행", False, _github_http_problem(res.status_code, "실행 목록"))
        )
    else:
        data = _json(res)
        total = data.get("total_count") if isinstance(data, dict) else None
        if isinstance(total, int):
            checks.append(Check("runs", "최근 실행", True, f"실행 기록 {total:,}건을 읽었어요"))
        else:
            checks.append(Check("runs", "최근 실행", False, "응답에 total_count가 없어요"))
    return checks


def _jenkins_job_path(job: str) -> str:
    """`폴더/작업`은 `job/폴더/job/작업`으로 바꾼다."""
    parts = [p for p in job.strip("/").split("/") if p]
    return "/".join(f"job/{quote(p, safe='')}" for p in parts)


def _param_names(body: Any) -> list[str]:
    names: list[str] = []
    props = body.get("property") if isinstance(body, dict) else None
    for prop in props if isinstance(props, list) else []:
        defs = prop.get("parameterDefinitions") if isinstance(prop, dict) else None
        for d in defs if isinstance(defs, list) else []:
            if isinstance(d, dict) and isinstance(d.get("name"), str):
                names.append(d["name"])
    return names


async def _jenkins_checks(
    config: dict[str, Any], secrets: dict[str, str], client: httpx.AsyncClient
) -> list[Check]:
    url, job = _s(config, "url").rstrip("/"), _s(config, "job")
    param = _s(config, "version_param", "version")
    if not job:
        return [Check("job", "Jenkins 작업", False, "작업 이름을 입력해 주세요")]
    problem = _host_problem(url)
    if problem:
        return [Check("job", "Jenkins 작업", False, problem)]
    auth = httpx.BasicAuth(_s(config, "username"), secrets.get("api_token", ""))
    res, error = await _send(
        client,
        "GET",
        f"{url}/{_jenkins_job_path(job)}/api/json",
        params={"tree": "property[parameterDefinitions[name]],builds[number]{0,1}"},
        auth=auth,
    )
    if error or res is None:
        return [Check("job", "Jenkins 작업", False, error or "")]
    if res.status_code in (401, 403):
        return [
            Check(
                "job",
                "Jenkins 작업",
                False,
                f"인증에 실패했어요 (HTTP {res.status_code}). "
                "사용자 이름과 API 토큰을 확인해 주세요",
            )
        ]
    if res.status_code == 404:
        return [
            Check(
                "job",
                "Jenkins 작업",
                False,
                f"작업 {job}을 찾을 수 없어요 (HTTP 404). "
                "작업 이름(폴더는 /로 구분)을 확인해 주세요",
            )
        ]
    if res.status_code != 200:
        return [Check("job", "Jenkins 작업", False, f"HTTP {res.status_code}")]
    body = _json(res)
    if not isinstance(body, dict):
        return [Check("job", "Jenkins 작업", False, "JSON 응답이 아니에요. 주소를 확인해 주세요")]
    checks = [Check("job", "Jenkins 작업", True, f"작업 {job}을 읽었어요")]
    if param in _param_names(body):
        checks.append(Check("param", "버전 매개변수", True, f"{param} 매개변수가 있어요"))
    else:
        checks.append(
            Check(
                "param",
                "버전 매개변수",
                False,
                f"작업에 {param} 매개변수가 없어요. 작업 설정의 '빌드 매개변수화'에 "
                f"문자열 매개변수 {param}을 추가해 주세요",
            )
        )
    builds = body.get("builds")
    if isinstance(builds, list):
        last = builds[0].get("number") if builds and isinstance(builds[0], dict) else None
        detail = f"최근 빌드 #{last}를 읽었어요" if last is not None else "아직 빌드가 없어요"
        checks.append(Check("builds", "최근 빌드", True, detail))
    else:
        checks.append(Check("builds", "최근 빌드", False, "응답에 builds가 없어요"))
    return checks


async def _dejafix_checks(config: dict[str, Any], secrets: dict[str, str]) -> list[Check]:
    # 로그인 경로·본문은 dejafix-proxy `DejaFixSession._login`과 같다
    # (패키지끼리 import하지 않아 복제)
    problem = _host_problem(_s(config, "url").rstrip("/"))
    if problem:
        return [Check("login", "로그인", False, problem)]
    async with _client(base_url=_s(config, "url").rstrip("/")) as client:
        res, error = await _send(
            client,
            "POST",
            "/api/auth/login",
            json={"username": _s(config, "username"), "password": secrets.get("password", "")},
        )
        if error or res is None:
            return [Check("login", "로그인", False, error or "")]
        token = None
        if res.status_code == 200:
            body = _json(res)
            data = body.get("data") if isinstance(body, dict) else None
            token = data.get("token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            why = f"HTTP {res.status_code}" if res.status_code != 200 else "응답에 토큰이 없어요"
            return [
                Check(
                    "login",
                    "로그인",
                    False,
                    f"로그인에 실패했어요 ({why}). 계정 이름과 비밀번호를 확인해 주세요",
                )
            ]
        checks = [Check("login", "로그인", True, "로그인했어요")]
        res, error = await _send(
            client,
            "GET",
            "/api/incidents",
            params={"status": "problem"},
            headers={"Authorization": f"Bearer {token}"},
        )
        if error or res is None:
            checks.append(Check("incidents", "장애 목록", False, error or ""))
        elif res.status_code != 200:
            checks.append(Check("incidents", "장애 목록", False, f"HTTP {res.status_code}"))
        else:
            body = _json(res)
            rows = body.get("data") if isinstance(body, dict) else None
            if isinstance(rows, list):
                checks.append(Check("incidents", "장애 목록", True, f"현재 장애 {len(rows)}건"))
            else:
                checks.append(
                    Check("incidents", "장애 목록", False, "응답의 data가 목록이 아니에요")
                )
        return checks


async def run_checks(
    kind: str, config: dict[str, Any], secrets: dict[str, str], *, lookup: Lookup
) -> list[Check]:
    """종류별로 도구를 불러 항목마다 통과 여부와 이유를 낸다. 비밀 값은 문구에서 가린다."""
    scrub = _Scrub(*secrets.values())
    checks: list[Check]
    if kind == "github_app":
        base = _s(config, "base_url", GITHUB_API).rstrip("/")
        conn = ToolConnection("", kind, "", config, dict(secrets))
        async with _client() as client:
            if base != GITHUB_API.rstrip("/") and not base.startswith(("http://", "https://")):
                return [
                    Check("token", "설치 토큰", False, "GitHub 주소는 http(s)://로 시작해야 해요")
                ]
            checks, _, _ = await _github_app_checks(conn, client, scrub)
    elif kind == "github_actions":
        async with _client() as client:
            checks = await _github_actions_checks(config, client, lookup, scrub)
    elif kind == "jenkins":
        scrub.add(secrets.get("api_token", ""))
        async with _client() as client:
            checks = await _jenkins_checks(config, secrets, client)
    elif kind == "dejafix":
        checks = await _dejafix_checks(config, secrets)
    else:
        return [Check("kind", "연결 종류", False, f"모르는 연결 종류예요: {kind}")]
    return [Check(c.key, c.label, c.ok, scrub(c.detail)) for c in checks]
