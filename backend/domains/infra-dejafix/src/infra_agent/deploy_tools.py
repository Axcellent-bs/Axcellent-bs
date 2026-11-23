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
- 다시 배포 요청이 시간 초과면 요청이 이미 들어갔을 수 있다. 오류를 내기 전에 요청 직전 이후에 생긴
  같은 버전 실행(GitHub: 제목의 버전, Jenkins: 대기열·빌드의 버전 매개변수)을 찾아, 있으면 그 실행을
  쓴다(두 번 배포하지 않게). 없으면 지금처럼 `timeout` 오류이고 저절로 다시 보내지 않는다.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
from urllib.parse import quote, urlsplit

import httpx

from ax_agent_template import pinned_http, tool_connections
from ax_agent_template.company_connection import check_host
from ax_agent_template.github_app import GithubAppAuth
from ax_agent_template.tool_connections import ToolConnection, ToolConnectionError

GITHUB_API = "https://api.github.com"
_GH_HOST = httpx.URL(GITHUB_API).host  # 공용 API는 주소 정책 예외 (사용자가 넣은 주소만 검사)
TIMEOUT_SECONDS = 10.0
# 디스패치 뒤 새 실행을 찾는 횟수와 간격(초). 못 찾아도 다시 배포는 성공이다(실행 id만 비움)
DISPATCH_LOOKUPS = 3
DISPATCH_LOOKUP_WAIT = 1.0
# 디스패치 시각(GitHub `Date` 헤더, 초 단위)보다 이만큼 앞서 만들어진 실행까지 우리 것으로 본다
DISPATCH_SKEW = timedelta(seconds=2)
# 다시 배포 요청이 시간 초과일 때 실제로 들어갔는지 찾는 데 쓰는 전체 한도(초).
# 넘으면 원래 timeout 오류를 그대로 낸다
DISPATCH_RECHECK_LIMIT = 15.0

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
    # 실행이 끝났나 (GitHub `status == completed`, Jenkins `result`가 있음). 모르면 None
    finished: bool | None = None


@dataclass(frozen=True)
class RunRef:
    """다시 배포로 띄운 실행. 도구가 아직 실행 id를 주지 않으면 `run_id`가 None일 수 있다."""

    tool: str
    run_id: str | None
    url: str | None


class DeployTool(Protocol):
    service: str
    tool: str
    # 연결에 적은 장애 부위(`config.components`, 쉼표로 나눔). 비었으면 이력이 다른 값을 쓴다
    components: tuple[str, ...]

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
        # 시간 초과는 요청이 이미 도착했을 수 있어 따로 구분한다 (다시 실행하기 전에 확인하도록)
        code = "timeout" if isinstance(exc, httpx.TimeoutException) else "unavailable"
        raise ToolConnectionError(code, f"{where} 연결 실패 ({type(exc).__name__})") from None
    if res.status_code >= 400:
        raise ToolConnectionError(_status_code(res.status_code), f"{where} → {res.status_code}")
    return res


def _list(value: Any) -> list[Any]:
    """목록이면 그대로, 아니면 빈 목록 (응답 모양이 달라도 TypeError를 내지 않게)."""
    return value if isinstance(value, list) else []


async def _recheck(lookup: Awaitable[Any]) -> Any:
    """시간 초과 뒤 확인을 한도 안에서 돌린다. 한도를 넘거나 응답 모양이 달라 깨지면 None
    (못 찾음: 부른 쪽이 원래 timeout 오류를 그대로 낸다)."""
    try:
        return await asyncio.wait_for(lookup, DISPATCH_RECHECK_LIMIT)
    except (TimeoutError, ToolConnectionError, TypeError, ValueError, AttributeError, KeyError):
        return None


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


def _components(conn: ToolConnection) -> tuple[str, ...]:
    """`config.components`("payment, checkout") → ("payment", "checkout"). 빈 값은 뺀다."""
    parts = (p.strip() for p in _text(conn, "components").split(","))
    return tuple(dict.fromkeys(p for p in parts if p))


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
        finished=done,
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
        self.components = _components(conn)

    def __repr__(self) -> str:
        return f"GithubActionsTool(service={self.service!r}, repo={self.repo!r})"

    def describe(self, version: str) -> str:
        return (
            f"GitHub Actions에서 {self.repo}의 {self.workflow} 워크플로를 "
            f"{self.version_input}={version}으로 다시 실행"
        )

    def _client(self) -> httpx.AsyncClient:
        # 사용자가 넣은 주소는 검사한 IP로만 붙고(DNS 재바인딩 차단) 리다이렉트를 따라가지 않는다
        transport = self._transport or pinned_http.guarded_transport(exempt=[_GH_HOST])
        return httpx.AsyncClient(
            transport=transport, timeout=TIMEOUT_SECONDS, follow_redirects=False
        )

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
            repo_res = await _send(
                client, "GET", f"{base}/repos/{self.repo}", where, headers=headers
            )
            branch = _json(repo_res, where).get("default_branch")
            if not isinstance(branch, str) or not branch:
                raise ToolConnectionError("unavailable", f"{where} 기본 브랜치가 없다")
            where = f"POST {self._workflow_path()}/dispatches"
            try:
                res = await _send(
                    client,
                    "POST",
                    f"{base}{self._workflow_path()}/dispatches",
                    where,
                    headers=headers,
                    json={"ref": branch, "inputs": {self.version_input: version}},
                )
            except ToolConnectionError as exc:
                if exc.code != "timeout":
                    raise
                # 요청이 들어갔을 수 있다: 디스패치 직전(레포 응답의 GitHub 시각) 이후에 생긴
                # 같은 버전 실행이 있으면 그것을 쓴다.
                # 없으면 같은 timeout 오류 (다시 보내지 않는다).
                # GitHub 시각을 모르면 기준을 세울 수 없어 찾지 않는다
                before = _header_time(repo_res)
                if before is None:
                    raise
                landed = await _recheck(
                    self._find_dispatched(
                        client, base, headers, branch, before - DISPATCH_SKEW, version=version
                    )
                )
                if landed is None:
                    raise
                return RunRef(tool=self.tool, run_id=landed.run_id, url=landed.url)
            ref = self._ref_from_dispatch(res)
            if ref is not None:
                return ref
            since = _server_time(res) - DISPATCH_SKEW
            # 같은 버전 실행만 우리 것으로 본다 (그 사이 다른 사람이 띄운 실행을 고르지 않게).
            # 제목에 버전이 없는 워크플로면 실행 id를 비워 둔다 (도구에서 확인)
            found = await self._find_dispatched(
                client, base, headers, branch, since, version=version
            )
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
        *,
        version: str | None = None,
    ) -> ToolRun | None:
        """디스패치 뒤에 생긴 `workflow_dispatch` 실행 중 가장 먼저 생긴 것 (우리가 띄운 실행).

        `version`을 주면 실행 제목의 버전이 그 값과 같은 실행만 본다 (시간 초과 뒤 확인용:
        제목에 버전이 없는 실행은 우리 것인지 알 수 없어 고르지 않는다).
        """
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
                for raw in _list(rows)
                if isinstance(raw, dict)
                and (created := _iso(raw.get("created_at"))) is not None
                and created >= since
                and (version is None or _title_version(raw) == version)
            ]
            if fresh:
                return _actions_run(min(fresh, key=lambda pair: pair[0])[1])
        return None


def _header_time(res: httpx.Response) -> datetime | None:
    """응답을 보낸 서버 시각 (`Date` 머리글). 없거나 틀리면 None."""
    try:
        at = parsedate_to_datetime(res.headers.get("date", ""))
    except (TypeError, ValueError):
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def _server_time(res: httpx.Response) -> datetime:
    """디스패치를 받은 GitHub 서버 시각 (`Date` 머리글). 없으면 지금."""
    return _header_time(res) or datetime.now(UTC)


# ---------- Jenkins ----------


def _job_path(job: str) -> str:
    """`folder/name` → `/job/folder/job/name`. 화면에서 `job/folder/job/name`을 넣어도 된다."""
    parts = [p for p in job.split("/") if p]
    if len(parts) > 1 and parts[0] == "job":
        parts = parts[1::2]
    return "".join(f"/job/{quote(p, safe='')}" for p in parts)


def _jenkins_param(build: dict[str, Any], name: str) -> str | None:
    for action in _list(build.get("actions")):
        if not isinstance(action, dict):
            continue
        for param in _list(action.get("parameters")):
            if isinstance(param, dict) and param.get("name") == name:
                value = param.get("value")
                return None if value is None or value == "" else str(value)
    return None


def _jenkins_actor(build: dict[str, Any]) -> str | None:
    for action in _list(build.get("actions")):
        if not isinstance(action, dict):
            continue
        for cause in _list(action.get("causes")):
            if isinstance(cause, dict) and isinstance(cause.get("userId"), str):
                return str(cause["userId"])
    return None


class JenkinsTool:
    """`jenkins` 연결 하나. API 토큰은 기본 인증으로만 보내고 다른 곳에 두지 않는다."""

    tool = "jenkins"

    def __init__(
        self,
        conn: ToolConnection,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[Any]] | None = None,
    ):
        self._conn = conn
        self._transport = transport
        self._sleep = sleep or asyncio.sleep
        self.service = _text(conn, "service")
        self.url = _text(conn, "url").rstrip("/")
        self.job = _text(conn, "job")
        # 화면에서 비워 두면 저장하지 않는다: 확인(dashboard-api)과 같은 기본값 version
        self.version_param = _text(conn, "version_param", "version")
        self.components = _components(conn)

    def __repr__(self) -> str:
        return f"JenkinsTool(service={self.service!r}, job={self.job!r})"

    def describe(self, version: str) -> str:
        return f"Jenkins에서 {self.job} 작업을 {self.version_param}={version}으로 다시 실행"

    async def resolve_run(self, run_id: str) -> str | None:
        """대기열 id(`queue-N`) → 빌드 번호 (`/queue/item/N/api/json`의 `executable.number`).

        아직 빌드가 시작되지 않았거나 읽지 못하면 None. 대기열 id가 아니면 그대로 돌려준다."""
        m = re.fullmatch(r"queue-(\d+)", run_id)
        if m is None:
            return run_id
        _, auth = await self._prepare()
        where = "GET /queue/item/{id}/api/json"
        async with self._client() as client:
            res = await _send(
                client, "GET", f"{self.url}/queue/item/{m.group(1)}/api/json", where, auth=auth
            )
        executable = _json(res, where).get("executable")
        number = executable.get("number") if isinstance(executable, dict) else None
        return str(number) if isinstance(number, int) else None

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
        # 사용자가 넣은 주소는 검사한 IP로만 붙고(DNS 재바인딩 차단) 리다이렉트를 따라가지 않는다
        transport = self._transport or pinned_http.guarded_transport(exempt=[_GH_HOST])
        return httpx.AsyncClient(
            transport=transport, timeout=TIMEOUT_SECONDS, follow_redirects=False
        )

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
                    finished=build.get("result") is not None,
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
            started = time.monotonic()
            try:
                # 버전은 쿼리 매개변수로 인코딩해 넣는다
                # (공백·`&`·한글이 다른 매개변수를 만들지 않게)
                res = await _send(
                    client,
                    "POST",
                    f"{job_url}/buildWithParameters",
                    where,
                    params={self.version_param: version},
                    auth=auth,
                )
            except ToolConnectionError as exc:
                if exc.code != "timeout":
                    raise
                # 요청이 들어갔을 수 있다: 요청 직전 이후 같은 버전이 대기열·빌드에 있으면
                # 그것을 쓴다. 없으면 같은 timeout 오류 (다시 보내지 않는다)
                landed = await _recheck(
                    self._find_dispatched(client, job_url, auth, version, started)
                )
                if landed is None:
                    raise
                return landed
        location = res.headers.get("location") or None
        queued = re.search(r"/queue/item/(\d+)", location or "")
        return RunRef(
            tool=self.tool, run_id=f"queue-{queued.group(1)}" if queued else None, url=location
        )

    async def _find_dispatched(
        self,
        client: httpx.AsyncClient,
        job_url: str,
        auth: httpx.BasicAuth,
        version: str,
        started: float,
    ) -> RunRef | None:
        """요청 직전(`started`, monotonic) 이후 대기열에 들어왔거나 시작한 같은 버전 빌드.

        시각은 Jenkins 시계로 비교한다: 조회 응답의 `Date`에서 요청 뒤 지난 시간을 빼
        요청 직전 Jenkins 시각을 구한다. `Date`가 없으면 기준을 세울 수 없어 None.
        대기열을 먼저 보고, 없으면 빌드를 본다.
        조회가 실패하면 None (요청이 들어갔는지 모른다).
        """
        job_path = urlsplit(job_url).path.rstrip("/")
        queue_where = "GET /queue/api/json"
        builds_where = f"GET /job/{self.job}/api/json"
        for _ in range(DISPATCH_LOOKUPS):
            await self._sleep(DISPATCH_LOOKUP_WAIT)
            try:
                queue_res = await _send(
                    client,
                    "GET",
                    f"{self.url}/queue/api/json",
                    queue_where,
                    params={
                        "tree": "items[id,inQueueSince,task[url],actions[parameters[name,value]]]"
                    },
                    auth=auth,
                )
                # `Date`는 대기열 응답의 시각이다: 지난 시간도 그 응답을 받은 때까지로 잰다
                answered = time.monotonic()
                items = _json(queue_res, queue_where).get("items")
                builds_res = await _send(
                    client,
                    "GET",
                    f"{job_url}/api/json",
                    builds_where,
                    params={
                        "tree": "builds[number,timestamp,url,actions[parameters[name,value]]]{0,10}"
                    },
                    auth=auth,
                )
                builds = _json(builds_res, builds_where).get("builds")
            except ToolConnectionError:
                return None
            since_ms = _jenkins_since_ms(queue_res, started, answered)
            if since_ms is None:
                return None
            queued = [
                item
                for item in _list(items)
                if isinstance(item, dict)
                and isinstance(item.get("id"), int)
                and isinstance(item.get("inQueueSince"), int)
                and item["inQueueSince"] >= since_ms
                and _task_path(item) == job_path
                and _jenkins_param(item, self.version_param) == version
            ]
            if queued:
                item = min(queued, key=lambda i: i["inQueueSince"])
                return RunRef(
                    tool=self.tool,
                    run_id=f"queue-{item['id']}",
                    url=f"{self.url}/queue/item/{item['id']}/",
                )
            started_builds = [
                build
                for build in _list(builds)
                if isinstance(build, dict)
                and isinstance(build.get("number"), int)
                and isinstance(build.get("timestamp"), int)
                and build["timestamp"] >= since_ms
                and _jenkins_param(build, self.version_param) == version
            ]
            if started_builds:
                build = min(started_builds, key=lambda b: b["timestamp"])
                url = build.get("url")
                return RunRef(
                    tool=self.tool,
                    run_id=str(build["number"]),
                    url=url if isinstance(url, str) else None,
                )
        return None


def _task_path(item: dict[str, Any]) -> str | None:
    task = item.get("task")
    url = task.get("url") if isinstance(task, dict) else None
    return urlsplit(url).path.rstrip("/") if isinstance(url, str) else None


def _jenkins_since_ms(res: httpx.Response, started: float, answered: float) -> int | None:
    """요청 직전 Jenkins 시각(ms)에서 여유를 뺀 값. `Date`가 없으면 None.

    응답 `Date`(초 단위) - (응답을 받은 때 - 요청 직전) - `DISPATCH_SKEW`.
    """
    server = _header_time(res)
    if server is None:
        return None
    elapsed = timedelta(seconds=max(0.0, answered - started))
    return int((server - elapsed - DISPATCH_SKEW).timestamp() * 1000)


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
