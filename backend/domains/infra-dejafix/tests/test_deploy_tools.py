"""배포 도구 어댑터 (GitHub Actions·Jenkins): 보관함 연결 → 최근 실행 읽기·다시 배포.

외부 호출은 모두 `httpx.MockTransport`로 받는다. 오류 문구에는 비밀 값이 없어야 한다.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from ax_agent_template import tool_connections as tc
from ax_agent_template.tool_connections import ToolConnection, ToolConnectionError
from infra_agent import deploy_tools
from infra_agent.deploy_tools import GithubActionsTool, JenkinsTool

PEM = (
    rsa.generate_private_key(public_exponent=65537, key_size=2048)
    .private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    .decode()
)
API = "https://api.github.com"
JENKINS = "http://127.0.0.1:8080"
JENKINS_TOKEN = "11aa22bb33cc44dd55ee"
INSTALL_TOKEN = "ghs_install_secret_123"

APP = ToolConnection(
    id="github_app-1",
    kind="github_app",
    name="회사 GitHub 앱",
    config={"app_id": "12345", "installation_id": "678"},
    secrets={"private_key": PEM},
)
ACTIONS = ToolConnection(
    id="github_actions-1",
    kind="github_actions",
    name="쇼핑몰 배포",
    config={
        "app": "github_app-1",
        "repo": "acme/shop",
        "workflow": "deploy.yml",
        "version_input": "version",
        "service": "shop-api",
    },
    secrets={},
)
JENKINS_CONN = ToolConnection(
    id="jenkins-1",
    kind="jenkins",
    name="사내 젠킨스",
    config={
        "url": JENKINS,
        "job": "shop-deploy",
        "version_param": "VERSION",
        "service": "shop-api",
        "username": "deployer",
    },
    secrets={"api_token": JENKINS_TOKEN},
)


@pytest.fixture(autouse=True)
def _fresh_auth() -> None:
    deploy_tools.reset_auth_cache()


async def _no_sleep(_: float) -> None:
    return None


def _token_response() -> httpx.Response:
    return httpx.Response(
        201, json={"token": INSTALL_TOKEN, "expires_at": "2099-01-01T00:00:00Z", "permissions": {}}
    )


def _run(run_id: int, title: str, sha: str, *, conclusion: str = "success", **extra) -> dict:
    return {
        "id": run_id,
        "display_title": title,
        "head_sha": sha,
        "status": "completed",
        "conclusion": conclusion,
        "event": "push",
        "created_at": "2026-11-16T01:00:00Z",
        "run_started_at": "2026-11-16T01:00:05Z",
        "updated_at": "2026-11-16T01:03:00Z",
        "html_url": f"https://github.com/acme/shop/actions/runs/{run_id}",
        "actor": {"login": "alice"},
        **extra,
    }


def test_actions_recent_maps_runs() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path.endswith("/access_tokens"):
            return _token_response()
        assert req.url.path == "/repos/acme/shop/actions/workflows/deploy.yml/runs"
        assert req.headers["Authorization"] == f"Bearer {INSTALL_TOKEN}"
        return httpx.Response(
            200,
            json={
                "workflow_runs": [
                    _run(3, "Deploy v2.4.1", "c" * 40),
                    _run(2, "결제 타임아웃 조정", "abcdef1234567890", conclusion="failure"),
                    _run(1, "fix things", "1234567abcdef"),
                ]
            },
        )

    tool = GithubActionsTool(ACTIONS, APP, transport=httpx.MockTransport(handler))

    runs = asyncio.run(tool.recent(limit=20))
    assert [(r.run_id, r.version, r.ok) for r in runs] == [
        ("3", "v2.4.1", True),  # 제목에 버전이 있으면 그 값
        ("2", "abcdef1", False),  # 없으면 커밋 앞 7자리
        ("1", "1234567", True),
    ]
    assert runs[0].at == datetime(2026, 11, 16, 1, 3, tzinfo=UTC)
    assert runs[0].actor == "alice"
    # 커밋 앞자리 버전은 표시만 하고 다시 배포 입력으로 쓰지 않게 표시한다
    assert [r.version_from_sha for r in runs] == [False, True, True]
    assert runs[0].url == "https://github.com/acme/shop/actions/runs/3"
    assert tool.service == "shop-api" and tool.tool == "github_actions"
    assert seen[1].url.params["per_page"] == "20"


def test_actions_redeploy_posts_dispatch_with_input() -> None:
    posted: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.endswith("/access_tokens"):
            return _token_response()
        if path == "/repos/acme/shop":
            return httpx.Response(200, json={"default_branch": "release"})
        if path.endswith("/dispatches"):
            posted.append(json.loads(req.content))
            return httpx.Response(204, headers={"Date": "Mon, 16 Nov 2026 02:00:00 GMT"})
        if path.endswith("/runs"):
            assert req.url.params["event"] == "workflow_dispatch"
            return httpx.Response(
                200,
                json={
                    "workflow_runs": [
                        _run(9, "Deploy", "d" * 40, created_at="2026-11-16T02:00:01Z"),
                        _run(8, "Deploy", "e" * 40, created_at="2026-11-16T01:00:00Z"),
                    ]
                },
            )
        raise AssertionError(path)

    tool = GithubActionsTool(ACTIONS, APP, transport=httpx.MockTransport(handler), sleep=_no_sleep)

    ref = asyncio.run(tool.redeploy("v2.4.0"))
    assert posted == [{"ref": "release", "inputs": {"version": "v2.4.0"}}]
    # 디스패치 뒤에 생긴 실행을 우리 실행으로 찾는다 (예전 실행 8은 아님)
    assert (ref.tool, ref.run_id) == ("github_actions", "9")
    assert ref.url == "https://github.com/acme/shop/actions/runs/9"
    assert "acme/shop" in tool.describe("v2.4.0") and "v2.4.0" in tool.describe("v2.4.0")


def test_jenkins_recent_reads_param() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/job/shop-deploy/api/json"
        assert "parameters[name,value]" in req.url.params["tree"]
        assert req.headers["Authorization"].startswith("Basic ")
        return httpx.Response(
            200,
            json={
                "builds": [
                    {
                        "number": 42,
                        "result": "SUCCESS",
                        "timestamp": 1_794_790_800_000,  # 2026-11-16T01:00:00Z
                        "duration": 60_000,
                        "url": f"{JENKINS}/job/shop-deploy/42/",
                        "actions": [
                            {"causes": [{"userId": "bob"}]},
                            {"parameters": [{"name": "VERSION", "value": "v2.4.1"}]},
                        ],
                    },
                    {
                        "number": 41,
                        "result": "FAILURE",
                        "timestamp": 1_794_787_200_000,
                        "duration": 1000,
                        "url": f"{JENKINS}/job/shop-deploy/41/",
                        "actions": [{"parameters": [{"name": "VERSION", "value": "v2.4.0"}]}],
                    },
                    {  # 버전 매개변수가 없는 빌드는 배포로 보지 않는다
                        "number": 40,
                        "result": "SUCCESS",
                        "timestamp": 1_794_783_600_000,
                        "duration": 1000,
                        "url": f"{JENKINS}/job/shop-deploy/40/",
                        "actions": [],
                    },
                ]
            },
        )

    tool = JenkinsTool(JENKINS_CONN, transport=httpx.MockTransport(handler))

    runs = asyncio.run(tool.recent())
    assert [(r.run_id, r.version, r.ok) for r in runs] == [
        ("42", "v2.4.1", True),
        ("41", "v2.4.0", False),
    ]
    assert runs[0].at == datetime(2026, 11, 16, 1, 1, tzinfo=UTC)
    assert runs[0].actor == "bob"
    assert runs[0].url == f"{JENKINS}/job/shop-deploy/42/"


def test_jenkins_redeploy_encodes_version() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(201, headers={"Location": f"{JENKINS}/queue/item/57/"})

    tool = JenkinsTool(JENKINS_CONN, transport=httpx.MockTransport(handler))

    ref = asyncio.run(tool.redeploy("v1 & 한글"))
    req = seen[0]
    assert req.method == "POST"
    assert req.url.path == "/job/shop-deploy/buildWithParameters"
    assert req.url.params["VERSION"] == "v1 & 한글"
    assert list(req.url.params.keys()) == ["VERSION"]  # `&`가 매개변수를 쪼개지 않는다
    assert b"%26" in req.url.query and "한글".encode() not in req.url.query
    assert (ref.tool, ref.run_id, ref.url) == ("jenkins", "queue-57", f"{JENKINS}/queue/item/57/")
    assert "shop-deploy" in tool.describe("v1") and "VERSION=v1" in tool.describe("v1")


def _vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, conns: list[ToolConnection]) -> None:
    path = tmp_path / "tool_connections.json"
    path.write_text(json.dumps([c._row() for c in conns]), encoding="utf-8")
    monkeypatch.setenv(tc.FILE_ENV, str(path))
    tc._cache = None


def test_tool_for_unknown_service_is_none(tmp_path, monkeypatch) -> None:
    _vault(tmp_path, monkeypatch, [APP, ACTIONS])
    assert deploy_tools.tool_for("support-api") is None
    found = deploy_tools.tool_for("shop-api")
    assert isinstance(found, GithubActionsTool)
    assert [t.tool for t in deploy_tools.all_tools()] == ["github_actions"]


def test_tool_for_without_vault_is_none(monkeypatch) -> None:
    monkeypatch.delenv(tc.FILE_ENV, raising=False)
    tc._cache = None
    assert deploy_tools.tool_for("shop-api") is None
    assert deploy_tools.all_tools() == []


def test_redeploy_error_has_no_secret() -> None:

    def jenkins_401(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f"bad token {JENKINS_TOKEN}")

    tool = JenkinsTool(JENKINS_CONN, transport=httpx.MockTransport(jenkins_401))
    with pytest.raises(ToolConnectionError) as e:
        asyncio.run(tool.redeploy("v2.4.0"))
    assert e.value.code == "unauthorized"
    assert JENKINS_TOKEN not in str(e.value)

    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"refused {JENKINS_TOKEN} {INSTALL_TOKEN}")

    tool = JenkinsTool(JENKINS_CONN, transport=httpx.MockTransport(boom))
    with pytest.raises(ToolConnectionError) as e:
        asyncio.run(tool.redeploy("v2.4.0"))
    assert JENKINS_TOKEN not in str(e.value)

    def github_dispatch_403(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/access_tokens"):
            return _token_response()
        if req.url.path == "/repos/acme/shop":
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(403, json={"message": f"token {INSTALL_TOKEN} lacks actions:write"})

    gh = GithubActionsTool(ACTIONS, APP, transport=httpx.MockTransport(github_dispatch_403))
    with pytest.raises(ToolConnectionError) as e:
        asyncio.run(gh.redeploy("v2.4.0"))
    assert e.value.code == "forbidden"
    message = str(e.value)
    assert INSTALL_TOKEN not in message and "PRIVATE KEY" not in message


def test_redeploy_refused_when_connection_check_failed() -> None:

    calls: list[httpx.Request] = []
    bad = ToolConnection(**{**JENKINS_CONN.__dict__, "check_ok": False})
    tool = JenkinsTool(
        bad, transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(201))
    )
    with pytest.raises(ToolConnectionError):
        asyncio.run(tool.redeploy("v2.4.0"))
    assert calls == []  # 확인 실패 상태면 부르지 않는다


def test_actions_without_app_connection_fails_at_use() -> None:

    tool = GithubActionsTool(
        ACTIONS, None, transport=httpx.MockTransport(lambda r: httpx.Response(500))
    )
    with pytest.raises(ToolConnectionError) as e:
        asyncio.run(tool.recent())
    assert e.value.code == "config"


def test_jenkins_outside_address_is_refused(monkeypatch) -> None:

    monkeypatch.delenv("AX_COMPANY_ALLOWED_HOSTS", raising=False)
    meta = ToolConnection(
        **{
            **JENKINS_CONN.__dict__,
            "config": {**JENKINS_CONN.config, "url": "http://169.254.169.254"},
        }
    )
    calls: list[httpx.Request] = []
    tool = JenkinsTool(
        meta, transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200))
    )
    with pytest.raises(ToolConnectionError) as e:
        asyncio.run(tool.recent())
    assert e.value.code == "config" and calls == []


def test_jenkins_without_version_param_uses_default_version() -> None:
    """화면에서 버전 매개변수를 비우면 저장되지 않는다: 확인과 같이 `version`을 쓴다."""
    blank = ToolConnection(
        **{
            **JENKINS_CONN.__dict__,
            "config": {k: v for k, v in JENKINS_CONN.config.items() if k != "version_param"},
        }
    )
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.method == "GET":
            build = {
                "number": 7,
                "result": "SUCCESS",
                "timestamp": 1_760_000_000_000,
                "duration": 1000,
                "actions": [{"parameters": [{"name": "version", "value": "v2.0.0"}]}],
            }
            return httpx.Response(200, json={"builds": [build]})
        return httpx.Response(201, headers={"Location": f"{JENKINS}/queue/item/9/"})

    tool = JenkinsTool(blank, transport=httpx.MockTransport(handler))
    assert tool.version_param == "version"
    assert [r.version for r in asyncio.run(tool.recent())] == ["v2.0.0"]
    asyncio.run(tool.redeploy("v1.9.0"))
    assert seen[-1].url.params["version"] == "v1.9.0"
    assert "version=v1" in tool.describe("v1")


def test_components_come_from_config() -> None:
    """`config.components`는 쉼표로 나눠 장애 부위로 쓴다. 비우면 빈 값(이력이 다른 값을 쓴다)."""
    config = {**JENKINS_CONN.config, "components": " payment, ,api"}
    named = ToolConnection(**{**JENKINS_CONN.__dict__, "config": config})
    assert JenkinsTool(named).components == ("payment", "api")
    assert JenkinsTool(JENKINS_CONN).components == ()
    actions = ToolConnection(
        **{**ACTIONS.__dict__, "config": {**ACTIONS.config, "components": "checkout"}}
    )
    assert GithubActionsTool(actions, APP).components == ("checkout",)
    assert GithubActionsTool(ACTIONS, APP).components == ()


# ---------- 디스패치 시간 초과: 실제로 들어갔는지 먼저 찾고, 두 번 보내지 않는다 ----------


def _actions_timeout_handler(posted: list[dict[str, Any]], runs: list[dict] | None) -> Any:
    """디스패치 POST는 시간 초과, 실행 목록은 `runs` (None이면 목록 읽기도 실패)."""

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.endswith("/access_tokens"):
            return _token_response()
        if path == "/repos/acme/shop":
            return httpx.Response(
                200,
                json={"default_branch": "release"},
                headers={"Date": "Mon, 16 Nov 2026 02:00:00 GMT"},
            )
        if path.endswith("/dispatches"):
            posted.append(json.loads(req.content))
            raise httpx.ReadTimeout("dispatch timed out", request=req)
        if path.endswith("/runs"):
            assert req.url.params["event"] == "workflow_dispatch"
            if runs is None:
                return httpx.Response(503)
            return httpx.Response(200, json={"workflow_runs": runs})
        raise AssertionError(path)

    return handler


def test_actions_dispatch_timeout_uses_landed_run() -> None:
    """(a) POST가 시간 초과여도 같은 버전 실행이 디스패치 뒤에 생겼으면 그 실행을 쓴다."""
    posted: list[dict[str, Any]] = []
    runs = [
        # 디스패치 뒤에 생겼지만 버전이 다르다 (다른 사람이 띄운 실행)
        _run(10, "Deploy v2.5.0", "f" * 40, created_at="2026-11-16T02:00:02Z"),
        # 우리 실행: 같은 버전, 디스패치 뒤
        _run(9, "Deploy v2.4.0", "d" * 40, created_at="2026-11-16T02:00:03Z"),
        # 같은 버전이지만 디스패치 전 실행 (예전 배포)
        _run(8, "Deploy v2.4.0", "e" * 40, created_at="2026-11-16T01:00:00Z"),
    ]
    tool = GithubActionsTool(
        ACTIONS,
        APP,
        transport=httpx.MockTransport(_actions_timeout_handler(posted, runs)),
        sleep=_no_sleep,
    )

    ref = asyncio.run(tool.redeploy("v2.4.0"))
    assert len(posted) == 1  # 다시 보내지 않는다
    assert (ref.tool, ref.run_id) == ("github_actions", "9")
    assert ref.url == "https://github.com/acme/shop/actions/runs/9"


@pytest.mark.parametrize(
    "runs",
    [
        [],
        # 다른 버전이거나 디스패치 전 실행, 제목에 버전이 없는 실행은 우리 것으로 보지 않는다
        [
            _run(10, "Deploy v2.5.0", "f" * 40, created_at="2026-11-16T02:00:02Z"),
            _run(11, "Deploy", "a" * 40, created_at="2026-11-16T02:00:04Z"),
            _run(8, "Deploy v2.4.0", "e" * 40, created_at="2026-11-16T01:00:00Z"),
        ],
        None,  # 실행 목록 읽기도 실패
    ],
)
def test_actions_dispatch_timeout_without_run_keeps_timeout(runs) -> None:
    """(b) 들어간 실행을 못 찾으면 지금처럼 timeout 오류. 저절로 다시 보내지 않는다."""
    posted: list[dict[str, Any]] = []
    tool = GithubActionsTool(
        ACTIONS,
        APP,
        transport=httpx.MockTransport(_actions_timeout_handler(posted, runs)),
        sleep=_no_sleep,
    )
    with pytest.raises(ToolConnectionError) as e:
        asyncio.run(tool.redeploy("v2.4.0"))
    assert e.value.code == "timeout"
    assert len(posted) == 1
    assert INSTALL_TOKEN not in str(e.value)


JOB_URL = f"{JENKINS}/job/shop-deploy/"
# Jenkins 서버 시각 (조회 응답의 Date 머리글) = 2026-11-16T02:00:10Z
JENKINS_NOW_MS = 1_794_794_410_000


def _queue_item(item_id: int, version: str, at_ms: int, *, job: str = JOB_URL) -> dict:
    return {
        "id": item_id,
        "inQueueSince": at_ms,
        "task": {"url": job},
        "actions": [{"parameters": [{"name": "VERSION", "value": version}]}],
    }


def _jenkins_build(number: int, version: str, at_ms: int) -> dict:
    return {
        "number": number,
        "timestamp": at_ms,
        "url": f"{JOB_URL}{number}/",
        "actions": [{"parameters": [{"name": "VERSION", "value": version}]}],
    }


def _jenkins_timeout_handler(
    posted: list[httpx.Request], queue: list[dict] | None, builds: list[dict] | None
) -> Any:
    date = {"Date": "Mon, 16 Nov 2026 02:00:10 GMT"}

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.endswith("/buildWithParameters"):
            posted.append(req)
            raise httpx.ReadTimeout("queue timed out", request=req)
        if path == "/queue/api/json":
            if queue is None:
                return httpx.Response(503)
            return httpx.Response(200, json={"items": queue}, headers=date)
        if path == "/job/shop-deploy/api/json":
            if builds is None:
                return httpx.Response(503)
            return httpx.Response(200, json={"builds": builds}, headers=date)
        raise AssertionError(path)

    return handler


def test_jenkins_dispatch_timeout_uses_queued_item() -> None:
    """(a) Jenkins: POST가 시간 초과여도 같은 버전이 대기열에 들어갔으면 그 항목을 쓴다."""
    posted: list[httpx.Request] = []
    queue = [
        _queue_item(60, "v2.5.0", JENKINS_NOW_MS - 500),  # 다른 버전
        _queue_item(61, "v2.4.0", JENKINS_NOW_MS - 500, job=f"{JENKINS}/job/other/"),  # 다른 작업
        _queue_item(58, "v2.4.0", JENKINS_NOW_MS - 500),  # 우리 것
        _queue_item(50, "v2.4.0", JENKINS_NOW_MS - 3_600_000),  # 한 시간 전부터 대기
    ]
    tool = JenkinsTool(
        JENKINS_CONN,
        transport=httpx.MockTransport(_jenkins_timeout_handler(posted, queue, [])),
        sleep=_no_sleep,
    )

    ref = asyncio.run(tool.redeploy("v2.4.0"))
    assert len(posted) == 1
    assert (ref.tool, ref.run_id, ref.url) == ("jenkins", "queue-58", f"{JENKINS}/queue/item/58/")


def test_jenkins_dispatch_timeout_uses_started_build() -> None:
    """(a) Jenkins: 대기열을 이미 떠나 빌드가 시작됐으면 그 빌드를 쓴다."""
    posted: list[httpx.Request] = []
    builds = [
        _jenkins_build(44, "v2.5.0", JENKINS_NOW_MS - 400),  # 다른 버전
        _jenkins_build(43, "v2.4.0", JENKINS_NOW_MS - 300),  # 우리 것
        _jenkins_build(42, "v2.4.0", JENKINS_NOW_MS - 3_600_000),  # 예전 배포
    ]
    tool = JenkinsTool(
        JENKINS_CONN,
        transport=httpx.MockTransport(_jenkins_timeout_handler(posted, [], builds)),
        sleep=_no_sleep,
    )

    ref = asyncio.run(tool.redeploy("v2.4.0"))
    assert len(posted) == 1
    assert (ref.tool, ref.run_id, ref.url) == ("jenkins", "43", f"{JOB_URL}43/")


@pytest.mark.parametrize(
    ("queue", "builds"),
    [
        ([], []),
        (
            [_queue_item(60, "v2.5.0", JENKINS_NOW_MS - 500)],
            [_jenkins_build(42, "v2.4.0", JENKINS_NOW_MS - 3_600_000)],
        ),
        (None, None),  # 조회도 실패
    ],
)
def test_jenkins_dispatch_timeout_without_build_keeps_timeout(queue, builds) -> None:
    """(b) Jenkins: 못 찾으면 지금처럼 timeout 오류. 저절로 다시 보내지 않는다."""
    posted: list[httpx.Request] = []
    tool = JenkinsTool(
        JENKINS_CONN,
        transport=httpx.MockTransport(_jenkins_timeout_handler(posted, queue, builds)),
        sleep=_no_sleep,
    )
    with pytest.raises(ToolConnectionError) as e:
        asyncio.run(tool.redeploy("v2.4.0"))
    assert e.value.code == "timeout"
    assert len(posted) == 1
    assert JENKINS_TOKEN not in str(e.value)


def test_jenkins_normal_redeploy_makes_no_lookup() -> None:
    """(c) 정상 응답이면 지금처럼 POST 한 번만 하고 대기열 항목을 돌려준다."""
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(201, headers={"Location": f"{JENKINS}/queue/item/57/"})

    tool = JenkinsTool(JENKINS_CONN, transport=httpx.MockTransport(handler), sleep=_no_sleep)
    ref = asyncio.run(tool.redeploy("v2.4.0"))
    assert [r.method for r in seen] == ["POST"]
    assert ref.run_id == "queue-57"


# ---------- 시간 초과 뒤 확인의 한도·이상한 응답 (원래 timeout 오류를 바꾸지 않는다) ----------


def _gh_handler(
    posted: list[dict[str, Any]],
    runs_body: Any,
    *,
    date: bool = True,
    run_calls: list[httpx.Request] | None = None,
) -> Any:
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.endswith("/access_tokens"):
            return _token_response()
        if path == "/repos/acme/shop":
            headers = {"Date": "Mon, 16 Nov 2026 02:00:00 GMT"} if date else {}
            return httpx.Response(200, json={"default_branch": "release"}, headers=headers)
        if path.endswith("/dispatches"):
            posted.append(json.loads(req.content))
            raise httpx.ReadTimeout("dispatch timed out", request=req)
        if path.endswith("/runs"):
            if run_calls is not None:
                run_calls.append(req)
            return httpx.Response(200, json=runs_body)
        raise AssertionError(path)

    return handler


def _expect_timeout(tool: Any) -> None:
    with pytest.raises(ToolConnectionError) as e:
        asyncio.run(tool.redeploy("v2.4.0"))
    assert e.value.code == "timeout"


def test_actions_timeout_without_date_header_skips_lookup() -> None:
    """GitHub 시각(`Date`)이 없으면 기준 시각을 모른다: 찾지 않고 원래 timeout 오류."""
    posted: list[dict[str, Any]] = []
    run_calls: list[httpx.Request] = []
    landed = {
        "workflow_runs": [
            _run(9, "Deploy v2.4.0", "d" * 40, created_at="2026-11-16T02:00:03Z"),
        ]
    }
    handler = _gh_handler(posted, landed, date=False, run_calls=run_calls)
    tool = GithubActionsTool(ACTIONS, APP, transport=httpx.MockTransport(handler), sleep=_no_sleep)
    _expect_timeout(tool)
    assert len(posted) == 1 and run_calls == []


def test_jenkins_timeout_without_date_header_is_not_found() -> None:
    """Jenkins 대기열 응답에 `Date`가 없으면 기준 시각을 모른다: 원래 timeout 오류."""
    posted: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.endswith("/buildWithParameters"):
            posted.append(req)
            raise httpx.ReadTimeout("queue timed out", request=req)
        if path == "/queue/api/json":
            # 이 서버 시계로는 방금 들어온 항목이다 (예전처럼 이 서버 시계로 바꿔 보면 찾아 버린다)
            item = _queue_item(58, "v2.4.0", int(time.time() * 1000))
            return httpx.Response(200, json={"items": [item]})
        if path == "/job/shop-deploy/api/json":
            return httpx.Response(200, json={"builds": []})
        raise AssertionError(path)

    tool = JenkinsTool(JENKINS_CONN, transport=httpx.MockTransport(handler), sleep=_no_sleep)
    _expect_timeout(tool)
    assert len(posted) == 1


@pytest.mark.parametrize(
    "runs_body",
    [
        {"workflow_runs": 5},
        {"workflow_runs": "x"},
        {"workflow_runs": [{"id": 9, "created_at": 7, "display_title": ["v2.4.0"]}]},
        [1, 2],
    ],
)
def test_actions_timeout_odd_runs_shape_keeps_timeout(runs_body) -> None:
    posted: list[dict[str, Any]] = []
    handler = _gh_handler(posted, runs_body)
    tool = GithubActionsTool(ACTIONS, APP, transport=httpx.MockTransport(handler), sleep=_no_sleep)
    _expect_timeout(tool)
    assert len(posted) == 1


@pytest.mark.parametrize(
    ("queue", "builds"),
    [
        (5, "x"),
        ([{"id": 58, "inQueueSince": JENKINS_NOW_MS, "task": "x", "actions": 5}], 3),
        (
            [{"id": 58, "inQueueSince": JENKINS_NOW_MS, "task": {"url": 9}, "actions": [5]}],
            [{"number": 43, "timestamp": JENKINS_NOW_MS, "actions": [{"parameters": 7}]}],
        ),
        (
            [{"id": 58, "inQueueSince": JENKINS_NOW_MS, "actions": [{"parameters": [3, None]}]}],
            [{"number": 43, "timestamp": "now", "actions": {"parameters": []}}],
        ),
    ],
)
def test_jenkins_timeout_odd_shape_keeps_timeout(queue, builds) -> None:
    posted: list[httpx.Request] = []
    tool = JenkinsTool(
        JENKINS_CONN,
        transport=httpx.MockTransport(_jenkins_timeout_handler(posted, queue, builds)),
        sleep=_no_sleep,
    )
    _expect_timeout(tool)
    assert len(posted) == 1


async def _slow_sleep(_: float) -> None:
    await asyncio.sleep(10)


def test_timeout_lookup_is_bounded(monkeypatch) -> None:
    """확인 전체에 한도를 둔다: 넘으면 원래 timeout 오류 (GitHub·Jenkins 모두)."""
    monkeypatch.setattr(deploy_tools, "DISPATCH_RECHECK_LIMIT", 0.05)
    posted: list[Any] = []
    gh = GithubActionsTool(
        ACTIONS,
        APP,
        transport=httpx.MockTransport(_gh_handler(posted, {"workflow_runs": []})),
        sleep=_slow_sleep,
    )
    jenkins = JenkinsTool(
        JENKINS_CONN,
        transport=httpx.MockTransport(_jenkins_timeout_handler(posted, [], [])),
        sleep=_slow_sleep,
    )
    started = time.monotonic()
    _expect_timeout(gh)
    _expect_timeout(jenkins)
    assert len(posted) == 2
    assert time.monotonic() - started < 5
