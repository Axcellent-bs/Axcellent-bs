"""도구 연결 종류별 확인: MockTransport로 통과·실패 이유와 비밀 가리기를 본다."""

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from ax_agent_template.tool_connections import ToolConnection
from ax_dashboard_api import tool_checks

TOKEN = "ghs_supersecrettoken123"
PASSWORD = "hunter2-very-secret"
API_TOKEN = "jenkins-api-token-xyz"


@pytest.fixture(scope="module")
def pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()


def _run(*args, **kwargs):
    return asyncio.run(tool_checks.run_checks(*args, **kwargs))


def _install(monkeypatch, handler) -> None:
    monkeypatch.setattr(tool_checks, "TRANSPORT", httpx.MockTransport(handler))


def _app(pem: str) -> ToolConnection:
    return ToolConnection(
        "github_app-1",
        "github_app",
        "회사 앱",
        {"app_id": "123", "installation_id": "456"},
        {"private_key": pem},
    )


def _expires() -> str:
    return (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


WORKFLOW = """\
name: deploy
run-name: "deploy ${{ inputs.version }}"
on:
  workflow_dispatch:
    inputs:
      version:
        required: true
jobs: {}
"""
WORKFLOW_NO_INPUT = "on:\n  workflow_dispatch:\n    inputs:\n      tag: {}\njobs: {}\n"

ACTIONS_CONFIG = {
    "app": "github_app-1",
    "repo": "acme/shop",
    "workflow": "deploy.yml",
    "service": "shop-web",
}


def _github(workflow: str, permissions: dict[str, str]):
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/access_tokens"):
            return httpx.Response(
                201, json={"token": TOKEN, "expires_at": _expires(), "permissions": permissions}
            )
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        if path == "/installation/repositories":
            return httpx.Response(200, json={"total_count": 3, "repositories": []})
        if path == "/repos/acme/shop/contents/.github/workflows/deploy.yml":
            content = base64.b64encode(workflow.encode()).decode()
            return httpx.Response(200, json={"content": content, "encoding": "base64"})
        if path == "/repos/acme/shop/actions/workflows/deploy.yml/runs":
            return httpx.Response(200, json={"total_count": 7, "workflow_runs": []})
        return httpx.Response(404, json={"message": "Not Found"})

    return handle


def _lookup(pem: str):
    app = _app(pem)
    return lambda cid: app if cid == app.id else None


def test_github_app_passes(monkeypatch, pem) -> None:
    _install(monkeypatch, _github(WORKFLOW, {"contents": "read", "actions": "write"}))
    checks = _run(
        "github_app",
        {"app_id": "123", "installation_id": "456"},
        {"private_key": pem},
        lookup=lambda _: None,
    )
    assert [c.key for c in checks] == ["token", "repos", "permissions"]
    assert all(c.ok for c in checks)
    assert "레포 3개" in checks[1].detail and "actions: write" in checks[2].detail


def test_actions_passes(monkeypatch, pem) -> None:
    _install(monkeypatch, _github(WORKFLOW, {"actions": "write"}))
    checks = _run("github_actions", ACTIONS_CONFIG, {}, lookup=_lookup(pem))
    assert all(c.ok for c in checks), checks
    keys = ["app", "permission", "workflow", "input", "run_name", "runs"]
    assert [c.key for c in checks] == keys


def test_actions_missing_version_input_says_how_to_fix(monkeypatch, pem) -> None:
    _install(monkeypatch, _github(WORKFLOW_NO_INPUT, {"actions": "write"}))
    checks = _run("github_actions", ACTIONS_CONFIG, {}, lookup=_lookup(pem))
    bad = next(c for c in checks if c.key == "input")
    assert not bad.ok and "workflow_dispatch" in bad.detail and "version" in bad.detail


@pytest.mark.parametrize(
    "run_name",
    [
        '"deploy ${{ inputs.version }}"',
        "deploy ${{inputs.version}}",
        '"${{ github.event.inputs.version }} 배포"',
        '"${{  github.event.inputs.version  }}"',
    ],
)
def test_actions_run_name_with_version_passes(monkeypatch, pem, run_name) -> None:
    workflow = WORKFLOW.replace('"deploy ${{ inputs.version }}"', run_name)
    _install(monkeypatch, _github(workflow, {"actions": "write"}))
    checks = _run("github_actions", ACTIONS_CONFIG, {}, lookup=_lookup(pem))
    assert next(c for c in checks if c.key == "run_name").ok, checks


@pytest.mark.parametrize(
    "run_name",
    [None, '"deploy ${{ github.sha }}"', '"deploy ${{ inputs.version_old }}"', "deploy version"],
)
def test_actions_run_name_without_version_fails_with_fix(monkeypatch, pem, run_name) -> None:
    line = 'run-name: "deploy ${{ inputs.version }}"\n'
    workflow = WORKFLOW.replace(line, "" if run_name is None else f"run-name: {run_name}\n")
    _install(monkeypatch, _github(workflow, {"actions": "write"}))
    checks = _run("github_actions", ACTIONS_CONFIG, {}, lookup=_lookup(pem))
    bad = next(c for c in checks if c.key == "run_name")
    assert not bad.ok
    assert "run-name" in bad.detail and "${{ inputs.version }}" in bad.detail
    assert "되돌릴 버전" in bad.detail


def test_actions_run_name_uses_custom_version_input(monkeypatch, pem) -> None:
    workflow = (
        'run-name: "deploy ${{ inputs.tag }}"\n'
        "on:\n  workflow_dispatch:\n    inputs:\n      tag: {}\njobs: {}\n"
    )
    _install(monkeypatch, _github(workflow, {"actions": "write"}))
    config = {**ACTIONS_CONFIG, "version_input": "tag"}
    checks = _run("github_actions", config, {}, lookup=_lookup(pem))
    assert all(c.ok for c in checks), checks


def test_actions_without_actions_write_permission(monkeypatch, pem) -> None:
    _install(monkeypatch, _github(WORKFLOW, {"actions": "read"}))
    checks = _run("github_actions", ACTIONS_CONFIG, {}, lookup=_lookup(pem))
    bad = next(c for c in checks if c.key == "permission")
    assert not bad.ok and "actions: write" in bad.detail


def test_actions_with_unknown_app(pem) -> None:
    checks = _run("github_actions", ACTIONS_CONFIG, {}, lookup=lambda _: None)
    assert len(checks) == 1 and not checks[0].ok and checks[0].key == "app"


JENKINS = {
    "url": "http://10.0.0.5:8080",
    "job": "deploy",
    "version_param": "version",
    "username": "bot",
    "service": "shop-web",
}


def _jenkins(params: list[str], status: int = 200):
    def handle(request: httpx.Request) -> httpx.Response:
        expected = "Basic " + base64.b64encode(f"bot:{API_TOKEN}".encode()).decode()
        assert request.headers["authorization"] == expected
        assert request.url.path == "/job/deploy/api/json"
        if status != 200:
            return httpx.Response(status, text=f"denied for {API_TOKEN}")
        body = {
            "property": [{"parameterDefinitions": [{"name": n} for n in params]}],
            "builds": [{"number": 41}],
        }
        return httpx.Response(200, json=body)

    return handle


def test_jenkins_passes(monkeypatch) -> None:
    _install(monkeypatch, _jenkins(["version"]))
    checks = _run("jenkins", JENKINS, {"api_token": API_TOKEN}, lookup=lambda _: None)
    assert all(c.ok for c in checks) and "#41" in checks[-1].detail


def test_jenkins_401(monkeypatch) -> None:
    _install(monkeypatch, _jenkins([], status=401))
    checks = _run("jenkins", JENKINS, {"api_token": API_TOKEN}, lookup=lambda _: None)
    assert len(checks) == 1 and not checks[0].ok and "401" in checks[0].detail
    assert API_TOKEN not in checks[0].detail


def test_jenkins_missing_param(monkeypatch) -> None:
    _install(monkeypatch, _jenkins(["branch"]))
    checks = _run("jenkins", JENKINS, {"api_token": API_TOKEN}, lookup=lambda _: None)
    bad = next(c for c in checks if c.key == "param")
    assert not bad.ok and "version" in bad.detail


DEJAFIX = {"url": "http://10.0.0.9:18000", "username": "viewer"}


def _dejafix(login_status: int = 200):
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            assert json.loads(request.content) == {"username": "viewer", "password": PASSWORD}
            if login_status != 200:
                return httpx.Response(login_status, json={"error": f"bad {PASSWORD}"})
            return httpx.Response(200, json={"data": {"token": "t-1"}})
        assert request.headers["authorization"] == "Bearer t-1"
        assert request.url.params["status"] == "problem"
        return httpx.Response(200, json={"data": [{"id": 1}, {"id": 2}]})

    return handle


def test_dejafix_passes(monkeypatch) -> None:
    _install(monkeypatch, _dejafix())
    checks = _run("dejafix", DEJAFIX, {"password": PASSWORD}, lookup=lambda _: None)
    assert all(c.ok for c in checks) and checks[-1].detail == "현재 장애 2건"


def test_dejafix_login_fails(monkeypatch) -> None:
    _install(monkeypatch, _dejafix(401))
    checks = _run("dejafix", DEJAFIX, {"password": PASSWORD}, lookup=lambda _: None)
    assert len(checks) == 1 and not checks[0].ok and "401" in checks[0].detail
    assert PASSWORD not in checks[0].detail


def test_timeout_detail(monkeypatch) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(f"timed out {API_TOKEN}")

    _install(monkeypatch, handle)
    checks = _run("jenkins", JENKINS, {"api_token": API_TOKEN}, lookup=lambda _: None)
    assert not checks[0].ok and checks[0].detail == "5초 안에 답이 없음"


def test_detail_never_contains_secret(monkeypatch, pem) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach with {API_TOKEN} and {PASSWORD}")

    _install(monkeypatch, handle)
    for kind, config, secrets in (
        ("jenkins", JENKINS, {"api_token": API_TOKEN}),
        ("dejafix", DEJAFIX, {"password": PASSWORD}),
        ("github_app", {"app_id": "1", "installation_id": "2"}, {"private_key": pem}),
    ):
        checks = _run(kind, config, secrets, lookup=lambda _: None)
        text = json.dumps([c.as_dict() for c in checks], ensure_ascii=False)
        assert API_TOKEN not in text and PASSWORD not in text and "PRIVATE KEY" not in text


def test_error_text_scrubbed_when_secret_in_message(monkeypatch) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"property": [], "builds": []})

    _install(monkeypatch, handle)
    cfg = {**JENKINS, "version_param": API_TOKEN}  # 문구에 값이 들어가는 경우
    checks = _run("jenkins", cfg, {"api_token": API_TOKEN}, lookup=lambda _: None)
    assert all(API_TOKEN not in c.detail for c in checks)
    assert any("••••" in c.detail for c in checks)


def test_stored_link_local_urls_make_no_request(monkeypatch, pem) -> None:
    sent: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(str(request.url))
        return httpx.Response(200, json={})

    _install(monkeypatch, handle)
    bad = "http://169.254.169.254"
    app = ToolConnection(
        "github_app-1",
        "github_app",
        "앱",
        {"app_id": "1", "installation_id": "2", "base_url": bad},
        {"private_key": pem},
    )
    runs = [
        ("jenkins", {**JENKINS, "url": bad}, {"api_token": API_TOKEN}),
        ("dejafix", {**DEJAFIX, "url": bad}, {"password": PASSWORD}),
        ("github_app", app.config, {"private_key": pem}),
        ("github_actions", ACTIONS_CONFIG, {}),
    ]
    for kind, config, secrets in runs:
        checks = _run(kind, config, secrets, lookup=lambda _, a=app: a)
        assert not all(c.ok for c in checks), kind
        assert "INVALID_URL" in json.dumps([c.detail for c in checks], ensure_ascii=False), kind
    assert sent == []


def test_github_401_body_with_token_is_not_in_details(monkeypatch, pem) -> None:
    fake = "ghs_FAKE_leaked_token_999"

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": f"Bad credentials {fake}"})

    _install(monkeypatch, handle)
    checks = _run(
        "github_app",
        {"app_id": "1", "installation_id": "2"},
        {"private_key": pem},
        lookup=lambda _: None,
    )
    text = json.dumps([c.as_dict() for c in checks], ensure_ascii=False)
    assert fake not in text and "401" in text


def test_short_secret_is_scrubbed(monkeypatch) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"property": [], "builds": []})

    _install(monkeypatch, handle)
    cfg = {**JENKINS, "version_param": "ab"}
    checks = _run("jenkins", cfg, {"api_token": "ab"}, lookup=lambda _: None)
    assert all("ab" not in c.detail.replace("••••", "") for c in checks)
