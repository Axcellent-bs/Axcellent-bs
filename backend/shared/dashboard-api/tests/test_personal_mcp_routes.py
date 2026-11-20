"""개인 MCP 연결 API (도구 연결 › 내 연결, 예진 결정 11/20).

- 내 AI 도구가 MCP로 붙을 개인 토큰: 발급(원문은 한 번만)·다시 발급(옛 것 바로 무효)·끊기
- 로그인 세션 본인만. 에이전트 토큰·공용 토큰은 403. 계정을 고르는 입력(경로·본문)은 없다
- 저장은 해시뿐. 회사 ID + 계정 ID에 묶인다
- 고른 AI 직원만, 회사 예산 중 내 몫(%) 안에서만 MCP 서버 검사(`/mcp/check`)를 통과한다
값은 명백한 더미만 쓴다.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_connection as cc
from ax_agent_template import human_auth, llm_usage
from ax_agent_template import tenant as tenants
from ax_contracts.data_access.llm_usage import LlmCallRecord
from ax_contracts.schemas import CompanyAiSettings
from ax_dashboard_api import accounts, auth, company_ai, personal_mcp
from ax_dashboard_api.main import app
from ax_secret_store import resolver

ME = "/api/dashboard/me/ai-connection"
CHECK = f"{ME}/mcp/check"
ADMIN = "/api/dashboard/company-ai"
VIEW = "/api/dashboard/ai-connection"
DEV = {"Authorization": "Bearer dev-agent-token"}

ROWS = [
    {"id": "a-1", "name": "민지", "email": "m@x.test", "role": "admin"},
    {"id": "a-2", "name": "태윤", "email": "t@x.test", "role": "admin"},
    {"id": "a-3", "name": "회원", "email": "h@x.test", "role": "member"},
    {"id": "a-4", "name": "총괄", "email": "o@x.test", "role": "operator"},
]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.delenv("DASHBOARD_MOCK", raising=False)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.setenv("DASHBOARD_HUMAN_TOKENS", "공용:shared-test-token")
    monkeypatch.setenv(
        "AX_AGENT_TOKENS", "orchestrator=orch-agent-token,dev=dev-agent-token"
    )
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(tmp_path / "company_connection.json"))
    monkeypatch.setattr(cc, "_cache", None)
    monkeypatch.setenv(tenants.TENANT_ENV, "catbooks")
    monkeypatch.delenv(tenants.TENANTS_DIR_ENV, raising=False)
    monkeypatch.delenv(company_ai.DIR_ENV, raising=False)
    monkeypatch.delenv(company_ai.TRIAL_KEY_ENV, raising=False)
    monkeypatch.delenv(company_ai.TRIAL_TOKENS_ENV, raising=False)
    monkeypatch.delenv("DATA_TENANT_TIMEZONE", raising=False)
    monkeypatch.delenv(accounts.ACCOUNTS_FILE_ENV, raising=False)
    monkeypatch.delenv(human_auth.REVOKED_FILE_ENV, raising=False)
    monkeypatch.delenv(human_auth.GENERATIONS_FILE_ENV, raising=False)
    monkeypatch.delenv(human_auth.ALLOW_SHARED_ENV, raising=False)
    monkeypatch.setenv(human_auth.SESSION_SECRET_ENV, "s" * 40)
    # 개인 MCP 연결은 키 저장소가 필요 없다(토큰 해시는 AI 설정 폴더에).
    # 에이전트 토큰 명부는 환경변수
    monkeypatch.setenv("AX_SECRET_STORE", "env")
    human_auth.reset_revoked_for_tests()
    resolver.reset_for_tests()
    llm_usage.set_store(llm_usage.SqliteUsageStore(":memory:"))
    yield tmp_path
    llm_usage.reset_store()
    resolver.reset_for_tests()


@pytest.fixture
def people(env: Path) -> dict[str, dict[str, str]]:
    (env / "accounts.json").write_text(json.dumps({"accounts": ROWS}), encoding="utf-8")
    out = {}
    for row in ROWS:
        token = human_auth.issue_session(account_id=row["id"], name=row["name"], role=row["role"])
        out[row["id"]] = {"Authorization": f"Bearer {token[0]}"}
    return out


@pytest.fixture
def client(env: Path) -> TestClient:
    c = TestClient(app)
    c.headers.pop("authorization", None)
    return c


def _issue(client: TestClient, who: dict, path: str = "token") -> Any:
    return client.post(f"{ME}/{path}", json={}, headers=who)


def _settings(client: TestClient, who: dict, **body: Any) -> Any:
    return client.post(f"{ME}/settings", json=body, headers=who)


def _check(client: TestClient, token: str, agent: str = "dev", who: dict = DEV) -> Any:
    return client.post(CHECK, json={"token": token, "agent": agent}, headers=who)


def _usage(account: str | None, tokens: int, tenant: str = "catbooks") -> None:
    store = llm_usage.get_store()
    assert store is not None
    now = datetime.now(UTC)
    store.write(
        LlmCallRecord(
            log_id=f"llm_{account}_{tokens}_{tenant}_{now.timestamp()}",
            tenant_id=tenant,
            occurred_at=now,
            source="data",
            department="data",
            model="m",
            input_tokens=tokens,
            output_tokens=0,
            success=True,
            account_id=account,
        )
    )


def _budget(env: Path, tokens: int | None, pct: int | None = None) -> None:
    """회사 AI 설정 파일(관리자 화면이 쓰는 것과 같은 자리·모양).

    관리자 저장 경로는 test_company_ai_routes가 본다.
    """
    settings = CompanyAiSettings(model="m", monthly_budget_tokens=tokens, monthly_limit_percent=pct)
    folder = env / "company_ai" / "catbooks"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / company_ai.SETTINGS_FILE).write_text(settings.model_dump_json(), encoding="utf-8")


# ---- 권한 ----


def test_needs_a_member_or_admin_login_session(client: TestClient, people: Any) -> None:
    assert client.get(ME).status_code == 401
    for token in ("shared-test-token", "orch-agent-token", "dev-agent-token"):
        who = {"Authorization": f"Bearer {token}"}
        assert client.get(ME, headers=who).status_code == 403
        for path in ("token", "token/reissue", "token/delete"):
            assert _issue(client, who, path).status_code == 403
        assert _settings(client, who, agents=["dev"]).status_code == 403
    assert client.get(ME, headers=people["a-4"]).status_code == 403  # 운영자는 고객사 회원이 아니다
    for who in ("a-1", "a-3"):
        assert client.get(ME, headers=people[who]).status_code == 200


def test_paths_fit_the_proxy_rule_and_name_no_account(client: TestClient, people: Any) -> None:
    """nginx: GET·HEAD·POST만, `me/ai-connection` 아래 [A-Za-z0-9_-] 최대 3단계. 계정 자리 없음."""
    import re

    paths = {p: ops for p, ops in app.openapi()["paths"].items() if p.startswith(ME)}
    assert sorted(paths) == [
        ME, f"{ME}/mcp", CHECK, f"{ME}/settings", f"{ME}/token", f"{ME}/token/delete",
        f"{ME}/token/reissue",
    ]
    for p in paths:
        rest = p[len(ME):]
        assert re.fullmatch(r"(/[A-Za-z0-9_-]+){0,3}", rest), p
    assert {m for ops in paths.values() for m in ops} <= {"get", "head", "post"}
    assert _settings(client, people["a-3"], agents=["dev"], account_id="a-1").status_code == 422
    assert ("POST", CHECK) in auth.MACHINE_ROUTES


# ---- 토큰 ----


def test_issue_shows_the_token_once_and_stores_only_a_hash(
    client: TestClient, people: Any, env: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    before = client.get(ME, headers=people["a-3"]).json()["data"]
    assert before["state"] == "not_issued"
    assert before["token"] == {"issued": False, "hint": "", "issued_at": None}
    res = _issue(client, people["a-3"])
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    token = data["token"]
    assert token.startswith("axm_") and len(token) >= 40
    assert data["hint"] == token[-4:]
    after = client.get(ME, headers=people["a-3"]).json()["data"]
    assert after["state"] == "connected"
    assert after["token"]["issued"] is True and after["token"]["hint"] == token[-4:]
    # 이미 있으면 그냥 발급은 막고 다시 발급을 쓰게 한다
    again = _issue(client, people["a-3"])
    assert again.status_code == 409 and again.json()["error"]["code"] == "TOKEN_EXISTS"
    # 원문은 다시 보이지 않는다: 보기 응답·파일·로그 어디에도 없다
    files = [p.read_text(encoding="utf-8") for p in env.rglob("*") if p.is_file()
             and p.suffix in (".json", ".jsonl")]
    text = "\n".join([client.get(ME, headers=people["a-3"]).text, *files]) + caplog.text
    assert token not in text
    assert personal_mcp.token_hash(token) in "\n".join(files)
    # 다른 사람에게는 보이지 않는다
    assert client.get(ME, headers=people["a-1"]).json()["data"]["token"]["issued"] is False


def test_reissue_kills_the_old_token_at_once(client: TestClient, people: Any) -> None:
    _settings(client, people["a-3"], agents=["dev"])
    old = _issue(client, people["a-3"]).json()["data"]["token"]
    r0 = _check(client, old)
    assert r0.status_code == 200, r0.text
    assert r0.json()["data"]["allowed"] is True
    res = _issue(client, people["a-3"], "token/reissue")
    assert res.status_code == 200
    new = res.json()["data"]["token"]
    assert new != old
    gone = _check(client, old).json()["data"]
    assert gone["allowed"] is False and gone["code"] == "TOKEN_UNKNOWN"
    assert _check(client, new).json()["data"]["allowed"] is True


def test_disconnect_drops_the_token(client: TestClient, people: Any) -> None:
    _settings(client, people["a-3"], agents=["dev"])
    token = _issue(client, people["a-3"]).json()["data"]["token"]
    res = _issue(client, people["a-3"], "token/delete")
    assert res.status_code == 200
    assert res.json()["data"] == {"issued": False}
    assert _check(client, token).json()["data"]["code"] == "TOKEN_UNKNOWN"
    assert client.get(ME, headers=people["a-3"]).json()["data"]["state"] == "not_issued"
    assert _issue(client, people["a-3"], "token/delete").status_code == 404


def test_one_persons_tokens_never_touch_anothers(client: TestClient, people: Any) -> None:
    for who in ("a-1", "a-3"):
        _settings(client, people[who], agents=["dev"])
    t1 = _issue(client, people["a-1"]).json()["data"]["token"]
    t3 = _issue(client, people["a-3"]).json()["data"]["token"]
    _issue(client, people["a-3"], "token/delete")
    got = _check(client, t1).json()["data"]
    assert got["allowed"] is True and got["account_id"] == "a-1" and got["name"] == "민지"
    assert _check(client, t3).json()["data"]["code"] == "TOKEN_UNKNOWN"


def test_token_is_tied_to_the_session_company(
    client: TestClient, people: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """회사는 세션의 회사(#1524). 같은 계정이라도 다른 회사 세션이면 다른 자리다."""
    _settings(client, people["a-3"], agents=["dev"])
    token = _issue(client, people["a-3"]).json()["data"]["token"]
    assert _check(client, token).json()["data"]["company_id"] == "catbooks"
    monkeypatch.setenv(tenants.TENANT_ENV, "other-co")  # 서버의 지금 회사가 바뀌어도 세션 회사
    assert client.get(ME, headers=people["a-3"]).json()["data"]["token"]["issued"] is True
    other, _ = human_auth.issue_session(
        account_id="a-3", name="회원", role="member", company_id="studyon"
    )[:2]
    there = {"Authorization": f"Bearer {other}"}
    got = client.get(ME, headers=there).json()["data"]
    assert got["company"]["id"] == "studyon" and got["token"]["issued"] is False
    assert got["settings"]["agents"] == []


def test_account_gone_means_the_token_stops(
    client: TestClient, people: Any, env: Path
) -> None:
    _settings(client, people["a-3"], agents=["dev"])
    token = _issue(client, people["a-3"]).json()["data"]["token"]
    rows = [r for r in ROWS if r["id"] != "a-3"]
    (env / "accounts.json").write_text(json.dumps({"accounts": rows}), encoding="utf-8")
    got = _check(client, token).json()["data"]
    assert got["allowed"] is False and got["code"] == "ACCOUNT_GONE"


# ---- MCP 서버 검사 ----


def test_check_is_for_the_mcp_server_agent_only(client: TestClient, people: Any) -> None:
    _settings(client, people["a-3"], agents=["dev"])
    token = _issue(client, people["a-3"]).json()["data"]["token"]
    assert _check(client, token, who={}).status_code == 401
    for who in (
        {"Authorization": "Bearer orch-agent-token"},  # 다른 에이전트
        {"Authorization": "Bearer shared-test-token"},
        people["a-3"],  # 사람 세션
        {"Authorization": f"Bearer {token}"},  # 개인 토큰 자체
    ):
        assert _check(client, token, who=who).status_code == 403, who
    assert _check(client, "axm_unknown").json()["data"]["code"] == "TOKEN_UNKNOWN"
    assert _check(client, token, agent="nope").status_code == 422


def test_only_chosen_ai_employees_pass(client: TestClient, people: Any) -> None:
    token = _issue(client, people["a-3"]).json()["data"]["token"]
    got = _check(client, token, "dev").json()["data"]
    assert got["allowed"] is False and got["code"] == "AGENT_NOT_CHOSEN" and got["message"]
    res = _settings(client, people["a-3"], agents=["planning", "dev", "planning"])
    assert res.status_code == 200
    assert res.json()["data"]["settings"]["agents"] == ["planning", "dev"]
    assert _check(client, token, "dev").json()["data"]["allowed"] is True
    assert _check(client, token, "planning").json()["data"]["allowed"] is True
    assert _check(client, token, "finance").json()["data"]["code"] == "AGENT_NOT_CHOSEN"
    assert _settings(client, people["a-3"], agents=["nope"]).status_code == 422


def test_choosable_agents_are_the_ten_ai_employees(client: TestClient, people: Any) -> None:
    got = client.get(ME, headers=people["a-3"]).json()["data"]
    assert got["agents_available"] == [
        "orchestrator", "support", "finance", "data", "hr",
        "marketing", "planning", "dev", "infra", "archive",
    ]


# ---- 한도(회사 예산 중 내 몫 %) ----


def test_share_percent_is_validated(client: TestClient, people: Any) -> None:
    for bad in (0, 101, -5):
        assert _settings(client, people["a-3"], monthly_share_percent=bad).status_code == 422
    res = _settings(client, people["a-3"], monthly_share_percent=20, agents=["dev"])
    assert res.json()["data"]["settings"]["monthly_share_percent"] == 20
    res = _settings(client, people["a-3"], monthly_share_percent=None, agents=["dev"])
    assert res.json()["data"]["settings"]["monthly_share_percent"] is None


def test_my_usage_is_shown_against_the_company_budget(
    client: TestClient, people: Any, env: Path
) -> None:
    _budget(env, 10_000)
    _settings(client, people["a-3"], agents=["dev"], monthly_share_percent=20)
    _usage("a-3", 500)
    _usage("a-1", 900)  # 다른 사람
    _usage(None, 5000)  # 화면·자동 일(사람 MCP 아님)
    _usage("a-3", 100, tenant="other-co")  # 다른 회사
    u = client.get(ME, headers=people["a-3"]).json()["data"]["usage"]
    assert u["recording"] is True
    assert u["used_tokens"] == 500
    assert u["budget_tokens"] == 10_000 and u["share_percent"] == 20
    assert u["limit_tokens"] == 2000
    assert u["percent_of_budget"] == 5.0  # 회사 예산 기준
    assert u["percent_of_limit"] == 25.0


def test_over_my_share_is_blocked_with_a_reason(
    client: TestClient, people: Any, env: Path
) -> None:
    _budget(env, 10_000)
    _settings(client, people["a-3"], agents=["dev"], monthly_share_percent=10)
    token = _issue(client, people["a-3"]).json()["data"]["token"]
    _usage("a-3", 999)
    assert _check(client, token).json()["data"]["allowed"] is True
    _usage("a-3", 1)
    got = _check(client, token).json()["data"]
    assert got["allowed"] is False and got["code"] == "LIMIT_REACHED"
    assert "10%" in got["message"]
    assert (got["used_tokens"], got["limit_tokens"]) == (1000, 1000)


def test_no_share_or_no_budget_means_no_personal_limit(client: TestClient, people: Any) -> None:
    _settings(client, people["a-3"], agents=["dev"], monthly_share_percent=10)
    token = _issue(client, people["a-3"]).json()["data"]["token"]
    _usage("a-3", 10**6)
    got = _check(client, token).json()["data"]  # 회사 예산이 없으면 % 계산 불가 → 막지 않는다
    assert got["allowed"] is True and got["limit_tokens"] is None
    u = client.get(ME, headers=people["a-3"]).json()["data"]["usage"]
    assert u["limit_tokens"] is None and u["percent_of_budget"] is None


def test_company_cap_blocks_mcp_too(
    client: TestClient, people: Any, env: Path
) -> None:
    _budget(env, 10_000, pct=50)
    _settings(client, people["a-3"], agents=["dev"])
    token = _issue(client, people["a-3"]).json()["data"]["token"]
    _usage(None, 5000)
    got = _check(client, token).json()["data"]
    assert got["allowed"] is False and got["code"] == "COMPANY_LIMIT_REACHED"


def test_mcp_usage_still_counts_in_the_company_budget(
    client: TestClient, people: Any, env: Path
) -> None:
    """MCP로 시킨 일도 회사 키로 돈다 → 회사 사용량에 그대로 센다(개인 키 때와 다르다)."""
    _budget(env, 10_000)
    _usage(None, 1000)
    _usage("a-3", 4000)
    st = client.get(f"{VIEW}/status", headers=people["a-3"]).json()["data"]["usage"]
    assert (st["used_tokens"], st["percent"]) == (5000, 50.0)
    assert client.get(f"{VIEW}/usage", headers=people["a-3"]).json()["data"]["total_tokens"] == 5000


def test_usage_recording_off_is_said_plainly(client: TestClient, people: Any) -> None:
    llm_usage.set_store(None)
    llm_usage.reset_store()
    u = client.get(ME, headers=people["a-3"]).json()["data"]["usage"]
    assert u["recording"] is False and u["used_tokens"] == 0 and u["percent_of_budget"] is None


def test_no_settings_folder_blocks_issuing_with_a_reason(
    client: TestClient, people: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(company_ai, "base_dir", lambda: None)  # AX_COMPANY_AI_DIR·연결 파일 없음
    got = client.get(ME, headers=people["a-3"]).json()["data"]
    assert got["can_save"] is False
    res = _issue(client, people["a-3"])
    assert res.status_code == 409 and res.json()["error"]["code"] == "COMPANY_AI_STORE_MISSING"


def test_settings_change_is_written_to_my_history(
    client: TestClient, people: Any, env: Path
) -> None:
    _settings(client, people["a-3"], agents=["dev"], monthly_share_percent=30)
    _issue(client, people["a-3"])
    _issue(client, people["a-3"], "token/reissue")
    _issue(client, people["a-3"], "token/delete")
    [hist] = list((env / "company_ai" / "catbooks" / "personal").glob("*.history.jsonl"))
    actions = [json.loads(line)["action"] for line in hist.read_text("utf-8").splitlines()]
    assert actions == ["settings", "token_issue", "token_reissue", "token_delete"]
    assert "a-3" not in hist.name
