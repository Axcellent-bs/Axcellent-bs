"""배포 도구 어댑터: 공용 보관함의 `github_actions`·`jenkins` 연결로 배포 이력을 읽고 다시 배포한다.

- 연결은 `ax_agent_template.tool_connections`(보관함)에서 부를 때마다 읽는다. 재시작 없이 반영된다.
  서비스(`config.service`)마다 도구 하나: 같은 서비스에 연결이 여럿이면 먼저 저장한 것을 쓴다.
- GitHub Actions: `github_app` 연결(설치 토큰)로 `GET .../workflows/{file}/runs`를 읽고,
  다시 배포는 `POST .../workflows/{file}/dispatches`
  `{ref: 기본 브랜치, inputs: {<버전 입력>: 버전}}`.
- Jenkins: `GET /job/<작업>/api/json?tree=builds[...]`의 버전 매개변수를 읽고, 다시 배포는
  `POST /job/<작업>/buildWithParameters?<매개변수>=<버전>` (사용자 + API 토큰 기본 인증).
- 사용자가 넣은 주소(Jenkins, GitHub Enterprise)는 부르기 직전에 `company_connection.check_host`를
  통과해야 한다 (dashboard-api 저장 정책과 같다).
- 오류는 `ToolConnectionError(code, message)`. 문구에 토큰·개인 키·응답 본문을 넣지 않는다.
- 다시 배포를 띄운 뒤 끝날 때까지 기다리지 않는다 (복구 확인은 `verify_recovery`가 지표로 한다).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
from urllib.parse import quote, urlsplit

import httpx

from ax_agent_template import tool_connections
from ax_agent_template.company_connection import check_host
from ax_agent_template.github_app import GithubAppAuth
from ax_agent_template.tool_connections import ToolConnection, ToolConnectionError

GITHUB_API = "https://api.github.com"
TIMEOUT_SECONDS = 10.0
# 디스패치 뒤 새 실행을 찾는 횟수와 간격(초). 못 찾아도 다시 배포는 성공이다(실행 id만 비움)
DISPATCH_LOOKUPS = 3
DISPATCH_LOOKUP_WAIT = 1.0
# 디스패치 시각(GitHub `Date` 헤더, 초 단위)보다 이만큼 앞서 만들어진 실행까지 우리 것으로 본다
DISPATCH_SKEW = timedelta(seconds=2)

_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_FILE = re.compile(r"[A-Za-z0-9_.-]+")
# 제목 속 버전: `v`로 시작하는 버전(v2, v2.4.1, v2.4.1-rc.1) 또는 점이 둘 이상인 숫자(2.4.1)
_VERSION = re.compile(
    r"(?<![\w.])(?:v\d+(?:\.\d+)*|\d+\.\d+\.\d+(?:\.\d+)?)(?:-[0-9A-Za-z.]+)?(?![\w])"
)


@dataclass(frozen=True)
class ToolRun:
    """배포 도구의 실행 한 번. `ok`는 끝났고 성공했을 때만 True."""

    version: str
    at: datetime
    ok: bool
    actor: str | None
    url: str | None
    run_id: str
    # 버전이 진짜 버전이 아니라 커밋 앞자리·실행 번호다 (GitHub 실행 제목에 버전이 없음).
    # 이 값을 다시 배포 입력으로 보내지 않는다
    version_from_sha: bool = False


@dataclass(frozen=True)
class RunRef:
    """다시 배포로 띄운 실행. 도구가 아직 실행 id를 주지 않으면 `run_id`가 None일 수 있다."""

    tool: str
    run_id: str | None
    url: str | None


class DeployTool(Protocol):
    service: str
    tool: str

    async def recent(self, limit: int = 20) -> list[ToolRun]: ...

    async def redeploy(self, version: str) -> RunRef: ...

    def describe(self, version: str) -> str:
        """사람에게 넘길 처리 순서 한 줄 ("<도구>에서 <작업>을 <버전>으로 다시 실행")."""
        ...


# ---------- 공용 ----------


def _status_code(status: int) -> str:
    return {401: "unauthorized", 403: "forbidden", 404: "not_found", 422: "invalid"}.get(
        status, "unavailable"
    )


async def _send(
    client: httpx.AsyncClient, method: str, url: str, where: str, **kwargs: Any
) -> httpx.Response:
    """부르고 실패를 `ToolConnectionError`로 바꾼다. 예외 문구·응답 본문은 넣지 않는다."""
    try:
        res = await client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise ToolConnectionError(
            "unavailable", f"{where} 연결 실패 ({type(exc).__name__})"
        ) from None
    if res.status_code >= 400:
        raise ToolConnectionError(_status_code(res.status_code), f"{where} → {res.status_code}")
    return res


def _json(res: httpx.Response, where: str) -> dict[str, Any]:
    try:
        body = res.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise ToolConnectionError("unavailable", f"{where} 응답 형식이 다르다")
    return body


async def _check_url(url: str, what: str) -> None:
    """사용자가 넣은 주소의 정책 검사 (사내 주소 또는 허용 목록 + https, 메타데이터 차단)."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ToolConnectionError("config", f"{what} 주소 형식이 다르다 (http(s)://호스트)")
    try:
        await asyncio.to_thread(check_host, url)
    except ValueError as exc:
        raise ToolConnectionError("config", f"{what} 주소를 쓸 수 없다: {exc}") from None


def _refuse_if_check_failed(*conns: ToolConnection | None) -> None:
    for conn in conns:
        if conn is not None and conn.check_ok is False:
            raise ToolConnectionError(
                "unchecked",
                f"'{conn.name}' 연결 확인이 실패한 상태라 다시 배포하지 않았다 "
                "(도구 연결에서 다시 확인)",
            )


def _iso(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def _text(conn: ToolConnection, key: str, default: str = "") -> str:
    value = conn.config.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else default


# ---------- GitHub Actions ----------

# 앱 연결마다 설치 토큰을 받아 둔다 (1시간짜리를 이력 받기마다 새로 받지 않게).
# 연결을 고치면 `updated_at`이 바뀌어 새로 만든다
_auths: dict[tuple[str, str | None], GithubAppAuth] = {}


def reset_auth_cache() -> None:
    """받아 둔 설치 토큰을 버린다 (테스트용)."""
    _auths.clear()


def _auth_for(app: ToolConnection) -> GithubAppAuth:
    key = (app.id, app.updated_at)
    auth = _auths.get(key)
    if auth is None:
        auth = GithubAppAuth(
            str(app.config.get("app_id") or ""),
            str(app.config.get("installation_id") or ""),
            app.secrets.get("private_key", ""),
        )
        _auths[key] = auth
    return auth


def _title_version(run: dict[str, Any]) -> str | None:
    title = run.get("display_title")
    m = _VERSION.search(title) if isinstance(title, str) else None
    return m.group(0) if m else None


def run_version(run: dict[str, Any]) -> str:
    """GitHub Actions 실행의 버전 표시 (휴리스틱).

    `workflow_dispatch` 입력 값은 실행 목록 응답에 나오지 않는다. 그래서
    1. 실행 제목(`display_title`)에 버전 꼴(`v2.4.1`, `v2`, `2.4.1`)이 있으면 그 값,
    2. 없으면 커밋(`head_sha`) 앞 7자리를 버전으로 본다.
    워크플로에 `run-name: Deploy ${{ inputs.version }}`을 두면 1번으로 정확해진다.
    2번이면 `ToolRun.version_from_sha`가 True라 그 버전으로는 다시 배포하지 않는다.
    우리가 롤백으로 띄운 실행은 이 값 대신 `DeploymentHistory.record_redeploy`에 남긴 버전으로
    바꿔 쓴다 (디스패치한 버전이 곧 그 실행의 버전이다).
    """
    if (found := _title_version(run)) is not None:
        return found
    sha = run.get("head_sha")
    return sha[:7] if isinstance(sha, str) and sha else f"run-{run.get('id')}"


def _actions_run(raw: Any) -> ToolRun | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), int | str):
        return None
    done = raw.get("status") == "completed"
    # 끝난 실행은 끝난 시각(새 버전이 돌기 시작한 때), 아니면 시작 시각
    at = (
        (_iso(raw.get("updated_at")) if done else None)
        or _iso(raw.get("run_started_at"))
        or _iso(raw.get("created_at"))
    )
    if at is None:
        return None
    who = raw.get("triggering_actor") or raw.get("actor") or {}
    actor = who.get("login") if isinstance(who, dict) else None
    url = raw.get("html_url")
    return ToolRun(
        version=run_version(raw),
        version_from_sha=_title_version(raw) is None,
        at=at,
        ok=done and raw.get("conclusion") == "success",
        actor=actor if isinstance(actor, str) else None,
        url=url if isinstance(url, str) else None,
        run_id=str(raw["id"]),
    )


class GithubActionsTool:
    """`github_actions` 연결 하나. 인증은 그 연결이 가리키는 `github_app` 연결로 한다."""

    tool = "github_actions"

    def __init__(
        self,
        conn: ToolConnection,
        app: ToolConnection | None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[Any]] | None = None,
    ):
        self._conn = conn
        self._app = app
        self._transport = transport
        self._sleep = sleep or asyncio.sleep
        self.service = _text(conn, "service")
        self.repo = _text(conn, "repo")
        self.workflow = _text(conn, "workflow")
        self.version_input = _text(conn, "version_input", "version")

    def __repr__(self) -> str:
        return f"GithubActionsTool(service={self.service!r}, repo={self.repo!r})"

    def describe(self, version: str) -> str:
        return (
            f"GitHub Actions에서 {self.repo}의 {self.workflow} 워크플로를 "
            f"{self.version_input}={version}으로 다시 실행"
        )

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=TIMEOUT_SECONDS)

    def _workflow_path(self) -> str:
        return f"/repos/{self.repo}/actions/workflows/{quote(self.workflow, safe='')}"

    async def _prepare(self, client: httpx.AsyncClient) -> tuple[str, dict[str, str]]:
        """(API 주소, 인증 머리글). 설정이 틀리면 부르기 전에 `config` 오류."""
        if not _REPO.fullmatch(self.repo):
            raise ToolConnectionError("config", f"'{self._conn.name}': 레포는 owner/name 꼴이다")
        if not _FILE.fullmatch(self.workflow):
            raise ToolConnectionError("config", f"'{self._conn.name}': 워크플로 파일 이름이 없다")
        if self._app is None:
            raise ToolConnectionError(
                "config", f"'{self._conn.name}'이 가리키는 GitHub 앱 연결이 없다"
            )
        base = (_text(self._app, "base_url") or GITHUB_API).rstrip("/")
        if base != GITHUB_API:
            await _check_url(base, "GitHub")
        token = await _auth_for(self._app).token(client, base)
        return base, {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def recent(self, limit: int = 20) -> list[ToolRun]:
        limit = max(1, min(limit, 100))
        where = f"GET {self._workflow_path()}/runs"
        async with self._client() as client:
            base, headers = await self._prepare(client)
            res = await _send(
                client,
                "GET",
                f"{base}{self._workflow_path()}/runs",
                where,
                headers=headers,
                params={"per_page": limit},
            )
            rows = _json(res, where).get("workflow_runs")
        if not isinstance(rows, list):
            raise ToolConnectionError("unavailable", f"{where} 응답 형식이 다르다")
        return [run for raw in rows if (run := _actions_run(raw)) is not None][:limit]

    async def redeploy(self, version: str) -> RunRef:
        if not version.strip():
            raise ToolConnectionError("invalid", "다시 배포할 버전이 없다")
        _refuse_if_check_failed(self._conn, self._app)
        async with self._client() as client:
            base, headers = await self._prepare(client)
            where = f"GET /repos/{self.repo}"
            repo = _json(
                await _send(client, "GET", f"{base}/repos/{self.repo}", where, headers=headers),
                where,
            )
            branch = repo.get("default_branch")
            if not isinstance(branch, str) or not branch:
                raise ToolConnectionError("unavailable", f"{where} 기본 브랜치가 없다")
            where = f"POST {self._workflow_path()}/dispatches"
            res = await _send(
                client,
                "POST",
                f"{base}{self._workflow_path()}/dispatches",
                where,
                headers=headers,
                json={"ref": branch, "inputs": {self.version_input: version}},
            )
            ref = self._ref_from_dispatch(res)
            if ref is not None:
                return ref
            since = _server_time(res) - DISPATCH_SKEW
            found = await self._find_dispatched(client, base, headers, branch, since)
        return RunRef(
            tool=self.tool,
            run_id=found.run_id if found else None,
            url=found.url if found else None,
        )

    def _ref_from_dispatch(self, res: httpx.Response) -> RunRef | None:
        """디스패치 응답이 실행 id를 주면(`return_run_details`를 켠 GitHub) 그 값을 쓴다."""
        if res.status_code != 200:
            return None
        try:
            body = res.json()
        except ValueError:
            return None
        run_id = body.get("workflow_run_id") if isinstance(body, dict) else None
        if not isinstance(run_id, int | str):
            return None
        url = body.get("html_url")
        return RunRef(tool=self.tool, run_id=str(run_id), url=url if isinstance(url, str) else None)

    async def _find_dispatched(
        self,
        client: httpx.AsyncClient,
        base: str,
        headers: dict[str, str],
        branch: str,
        since: datetime,
    ) -> ToolRun | None:
        """디스패치 뒤에 생긴 `workflow_dispatch` 실행 중 가장 먼저 생긴 것 (우리가 띄운 실행)."""
        where = f"GET {self._workflow_path()}/runs?event=workflow_dispatch"
        for _ in range(DISPATCH_LOOKUPS):
            await self._sleep(DISPATCH_LOOKUP_WAIT)
            try:
                res = await _send(
                    client,
                    "GET",
                    f"{base}{self._workflow_path()}/runs",
                    where,
                    headers=headers,
                    params={"event": "workflow_dispatch", "branch": branch, "per_page": 10},
                )
                rows = _json(res, where).get("workflow_runs")
            except ToolConnectionError:
                return None  # 디스패치는 이미 됐다: 실행 id만 모른다
            fresh = [
                (created, raw)
                for raw in rows or []
                if isinstance(raw, dict)
                and (created := _iso(raw.get("created_at"))) is not None
                and created >= since
            ]
            if fresh:
                return _actions_run(min(fresh, key=lambda pair: pair[0])[1])
        return None


def _server_time(res: httpx.Response) -> datetime:
    """디스패치를 받은 GitHub 서버 시각 (`Date` 머리글). 없으면 지금."""
    try:
        at = parsedate_to_datetime(res.headers.get("date", ""))
    except (TypeError, ValueError):
        return datetime.now(UTC)
    return at if at.tzinfo else at.replace(tzinfo=UTC)


# ---------- Jenkins ----------


def _job_path(job: str) -> str:
    """`folder/name` → `/job/folder/job/name`. 화면에서 `job/folder/job/name`을 넣어도 된다."""
    parts = [p for p in job.split("/") if p]
    if len(parts) > 1 and parts[0] == "job":
        parts = parts[1::2]
    return "".join(f"/job/{quote(p, safe='')}" for p in parts)


def _jenkins_param(build: dict[str, Any], name: str) -> str | None:
    for action in build.get("actions") or []:
        if not isinstance(action, dict):
            continue
        for param in action.get("parameters") or []:
            if isinstance(param, dict) and param.get("name") == name:
                value = param.get("value")
                return None if value is None or value == "" else str(value)
    return None


def _jenkins_actor(build: dict[str, Any]) -> str | None:
    for action in build.get("actions") or []:
        if not isinstance(action, dict):
            continue
        for cause in action.get("causes") or []:
            if isinstance(cause, dict) and isinstance(cause.get("userId"), str):
                return str(cause["userId"])
    return None


class JenkinsTool:
    """`jenkins` 연결 하나. API 토큰은 기본 인증으로만 보내고 다른 곳에 두지 않는다."""

    tool = "jenkins"

    def __init__(self, conn: ToolConnection, *, transport: httpx.AsyncBaseTransport | None = None):
        self._conn = conn
        self._transport = transport
        self.service = _text(conn, "service")
        self.url = _text(conn, "url").rstrip("/")
        self.job = _text(conn, "job")
        self.version_param = _text(conn, "version_param")

    def __repr__(self) -> str:
        return f"JenkinsTool(service={self.service!r}, job={self.job!r})"

    def describe(self, version: str) -> str:
        return f"Jenkins에서 {self.job} 작업을 {self.version_param}={version}으로 다시 실행"

    async def _prepare(self) -> tuple[str, httpx.BasicAuth]:
        if not self.job or not _job_path(self.job):
            raise ToolConnectionError("config", f"'{self._conn.name}': 작업 이름이 없다")
        if not self.version_param:
            raise ToolConnectionError("config", f"'{self._conn.name}': 버전 매개변수 이름이 없다")
        user, token = _text(self._conn, "username"), self._conn.secrets.get("api_token", "")
        if not user or not token:
            raise ToolConnectionError("config", f"'{self._conn.name}': 사용자·API 토큰이 없다")
        await _check_url(self.url, "Jenkins")
        return f"{self.url}{_job_path(self.job)}", httpx.BasicAuth(user, token)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=TIMEOUT_SECONDS)

    async def recent(self, limit: int = 20) -> list[ToolRun]:
        limit = max(1, min(limit, 100))
        job_url, auth = await self._prepare()
        tree = (
            "builds[number,result,timestamp,duration,url,"
            f"actions[parameters[name,value],causes[userId]]]{{0,{limit}}}"
        )
        where = f"GET /job/{self.job}/api/json"
        async with self._client() as client:
            res = await _send(
                client, "GET", f"{job_url}/api/json", where, params={"tree": tree}, auth=auth
            )
        builds = _json(res, where).get("builds")
        if not isinstance(builds, list):
            raise ToolConnectionError("unavailable", f"{where} 응답 형식이 다르다")
        runs: list[ToolRun] = []
        for build in builds:
            if not isinstance(build, dict) or not isinstance(build.get("number"), int):
                continue
            version = _jenkins_param(build, self.version_param)
            stamp = build.get("timestamp")
            if version is None or not isinstance(stamp, int):
                continue  # 버전 매개변수 없는 빌드는 되돌릴 수 있는 배포가 아니다
            duration = build.get("duration")
            end = stamp + (duration if isinstance(duration, int) else 0)
            url = build.get("url")
            runs.append(
                ToolRun(
                    version=version,
                    at=datetime.fromtimestamp(end / 1000, UTC),
                    ok=build.get("result") == "SUCCESS",
                    actor=_jenkins_actor(build),
                    url=url if isinstance(url, str) else None,
                    run_id=str(build["number"]),
                )
            )
        return runs

    async def redeploy(self, version: str) -> RunRef:
        if not version.strip():
            raise ToolConnectionError("invalid", "다시 배포할 버전이 없다")
        _refuse_if_check_failed(self._conn)
        job_url, auth = await self._prepare()
        where = f"POST /job/{self.job}/buildWithParameters"
        async with self._client() as client:
            # 버전은 쿼리 매개변수로 인코딩해 넣는다 (공백·`&`·한글이 다른 매개변수를 만들지 않게)
            res = await _send(
                client,
                "POST",
                f"{job_url}/buildWithParameters",
                where,
                params={self.version_param: version},
                auth=auth,
            )
        location = res.headers.get("location") or None
        queued = re.search(r"/queue/item/(\d+)", location or "")
        return RunRef(
            tool=self.tool, run_id=f"queue-{queued.group(1)}" if queued else None, url=location
        )


# ---------- 보관함 → 도구 ----------


def _build(conn: ToolConnection, conns: list[ToolConnection]) -> DeployTool | None:
    if conn.kind == "github_actions":
        app_id = conn.config.get("app")
        app = next((c for c in conns if c.kind == "github_app" and c.id == app_id), None)
        return GithubActionsTool(conn, app)
    if conn.kind == "jenkins":
        return JenkinsTool(conn)
    return None


def all_tools() -> list[DeployTool]:
    """보관함의 배포 도구 전부 (서비스마다 먼저 저장한 하나). 보관함이 없거나 깨졌으면 빈 목록."""
    conns = tool_connections.read_all()
    tools: list[DeployTool] = []
    seen: set[str] = set()
    for conn in conns:
        tool = _build(conn, conns)
        if tool is None or not tool.service or tool.service in seen:
            continue
        seen.add(tool.service)
        tools.append(tool)
    return tools


def tool_for(service: str) -> DeployTool | None:
    """그 서비스의 배포 도구. 연결이 없으면 None (시연 경로를 쓴다)."""
    return next((t for t in all_tools() if t.service == service), None)
