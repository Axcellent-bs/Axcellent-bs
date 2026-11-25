"""회사 AI 설정·AI 연결 API (회사 AI 연결 설계 7·8장, 예진 결정 11/20).

- 회사당 키 하나·모델 하나. 저장·교체·삭제·설정은 관리자, 상태·사용량은 회원 모두
- 처음 등록은 바로, 키 교체·삭제는 다른 관리자 승인(관리자 한 명뿐이면 바로 + 알림)
- 모델·예산 변경은 바로 + 알림. 키 값은 끝 4자리만. 예산은 토큰, 응답에 사용 %
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
from ax_dashboard_api import accounts, company_ai
from ax_dashboard_api.main import app
from ax_secret_store import resolver
from ax_secret_store.crypto import generate_master_key

ADMIN = "/api/dashboard/company-ai"
VIEW = "/api/dashboard/ai-connection"
DUMMY = "DUMMY-NOT-A-REAL-KEY-0001"
DUMMY2 = "DUMMY-NOT-A-REAL-KEY-0002"
KEY = "LLM_API_KEY"

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
    monkeypatch.setenv("AX_SECRET_STORE", "db")
    monkeypatch.setenv("AX_SECRET_MASTER_KEY", generate_master_key())
    monkeypatch.setenv("AX_SECRET_DB_FILE", str(tmp_path / "secrets.db"))
    monkeypatch.setenv("AX_SECRET_CACHE_TTL", "0")
    human_auth.reset_revoked_for_tests()
    resolver.reset_for_tests()
    store = llm_usage.SqliteUsageStore(":memory:")
    llm_usage.set_store(store)
    yield tmp_path
    llm_usage.reset_store()
    resolver.reset_for_tests()


def _people(tmp_path: Path, rows: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    (tmp_path / "accounts.json").write_text(json.dumps({"accounts": rows}), encoding="utf-8")
    out = {}
    for row in rows:
        token = human_auth.issue_session(account_id=row["id"], name=row["name"], role=row["role"])
        out[row["id"]] = {"Authorization": f"Bearer {token[0]}"}
    return out


@pytest.fixture
def people(env: Path) -> dict[str, dict[str, str]]:
    return _people(env, ROWS)


@pytest.fixture
def client(env: Path) -> TestClient:
    c = TestClient(app)
    c.headers.pop("authorization", None)
    return c


def _put_key(client: TestClient, who: dict, value: str = DUMMY, name: str = KEY) -> Any:
    return client.post(f"{ADMIN}/keys/{name}", json={"value": value}, headers=who)


def _usage(tenant: str, dept: str, tokens_in: int, tokens_out: int, at: datetime) -> None:
    store = llm_usage.get_store()
    assert store is not None
    store.write(
        LlmCallRecord(
            log_id=f"llm_{dept}_{at.timestamp()}_{tokens_in}",
            tenant_id=tenant,
            occurred_at=at,
            source=dept,
            department=dept,
            model="m",
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            success=True,
        )
    )


# ---- 권한 ----


def test_admin_routes_need_a_company_admin_session(client: TestClient, people: Any) -> None:
    assert client.get(ADMIN).status_code == 401
    shared = {"Authorization": "Bearer shared-test-token"}
    assert client.get(ADMIN, headers=shared).status_code == 403
    for who in ("a-3", "a-4"):  # 일반회원·운영자
        assert client.get(ADMIN, headers=people[who]).status_code == 403
        assert _put_key(client, people[who]).status_code == 403
        res = client.post(f"{ADMIN}/settings", json={"model": "m"}, headers=people[who])
        assert res.status_code == 403
    assert client.get(ADMIN, headers=people["a-1"]).status_code == 200


def test_writes_take_human_sessions_only_not_agent_or_shared_tokens(
    client: TestClient, people: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """키·설정 쓰기는 사람 로그인 세션만 받는다.

    AI 에이전트 토큰·공용 사람 토큰은 막는다(11/20 태윤 님 조건).
    """
    monkeypatch.setenv("AX_AGENT_TOKENS", "orchestrator=orch-agent-token,marketing=mkt-agent-token")
    _put_key(client, people["a-1"])
    req_id = _put_key(client, people["a-1"], DUMMY2).json()["data"]["request"]["id"]
    for token in ("orch-agent-token", "mkt-agent-token", "shared-test-token"):
        who = {"Authorization": f"Bearer {token}"}
        writes = [
            client.post(f"{ADMIN}/keys/{KEY}", json={"value": DUMMY2}, headers=who),
            client.post(f"{ADMIN}/keys/{KEY}/delete", json={}, headers=who),
            client.delete(f"{ADMIN}/keys/{KEY}", headers=who),
            client.post(f"{ADMIN}/settings", json={"model": "m"}, headers=who),
            client.post(f"{ADMIN}/requests/{req_id}/approve", headers=who),
            client.post(f"{ADMIN}/requests/{req_id}/reject", headers=who),
        ]
        for res in writes:
            assert res.status_code in (401, 403), (token, res.status_code, res.text)
    assert resolver.get_secret(KEY, tenant="catbooks") == DUMMY  # 아무것도 바뀌지 않았다


def test_replace_and_approve_never_leak_the_raw_key(
    client: TestClient, people: Any, env: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """교체 요청·승인·알림·기록·오류 어디에도 키 원문이 없다(끝 4자리만)."""
    caplog.set_level(logging.DEBUG)
    seen = [_put_key(client, people["a-1"]).text]
    res = _put_key(client, people["a-1"], DUMMY2)
    seen.append(res.text)
    req_id = res.json()["data"]["request"]["id"]
    seen.append(client.post(f"{ADMIN}/requests/{req_id}/approve", headers=people["a-1"]).text)
    seen.append(client.post(f"{ADMIN}/requests/{req_id}/approve", headers=people["a-2"]).text)
    seen.append(client.get(ADMIN, headers=people["a-2"]).text)
    seen.append(client.get(f"{VIEW}/status", headers=people["a-3"]).text)
    seen.append(
        client.post(f"{ADMIN}/keys/NOPE", json={"value": DUMMY}, headers=people["a-1"]).text
    )
    folder = env / "company_ai" / "catbooks"
    seen += [p.read_text(encoding="utf-8") for p in folder.iterdir()]
    text = "\n".join(seen) + caplog.text
    assert DUMMY not in text and DUMMY2 not in text


def test_every_member_reads_status_and_usage(client: TestClient, people: Any) -> None:
    assert client.get(f"{VIEW}/status").status_code == 401
    shared = {"Authorization": "Bearer shared-test-token"}
    assert client.get(f"{VIEW}/status", headers=shared).status_code == 403
    for who in ("a-1", "a-3", "a-4"):
        assert client.get(f"{VIEW}/status", headers=people[who]).status_code == 200
        assert client.get(f"{VIEW}/usage", headers=people[who]).status_code == 200


# ---- 키 ----


def test_first_key_is_applied_at_once_and_only_the_hint_comes_back(
    client: TestClient, people: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    res = _put_key(client, people["a-1"])
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["applied"] is True and data["op"] == "add"
    assert resolver.get_secret(KEY, tenant="catbooks") == DUMMY
    got = client.get(ADMIN, headers=people["a-1"])
    key = next(k for k in got.json()["data"]["keys"] if k["name"] == KEY)
    assert key["has_key"] is True and key["hint"] == DUMMY[-4:]
    status = client.get(f"{VIEW}/status", headers=people["a-3"])
    text = res.text + got.text + status.text + caplog.text
    assert DUMMY not in text


def test_key_files_never_hold_the_value(client: TestClient, people: Any, env: Path) -> None:
    _put_key(client, people["a-1"])
    client.post(
        f"{ADMIN}/settings", json={"model": "m", "monthly_budget_tokens": 10}, headers=people["a-1"]
    )
    folder = env / "company_ai" / "catbooks"
    assert (folder / "company_ai.json").is_file()
    assert (folder / "company_ai.history.jsonl").is_file()
    for path in folder.iterdir():
        assert DUMMY not in path.read_text(encoding="utf-8")


def test_unknown_key_name_is_rejected(client: TestClient, people: Any) -> None:
    res = _put_key(client, people["a-1"], name="AX_AGENT_TOKENS")
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "UNKNOWN_KEY_NAME"


def test_bad_value_is_rejected_without_echo(client: TestClient, people: Any) -> None:
    res = _put_key(client, people["a-1"], value="short")
    assert res.status_code == 422 and "short" not in res.text


def test_replace_waits_for_another_admin(client: TestClient, people: Any) -> None:
    assert _put_key(client, people["a-1"]).status_code == 200
    res = _put_key(client, people["a-1"], DUMMY2)
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["applied"] is False and data["op"] == "replace"
    req_id = data["request"]["id"]
    assert resolver.get_secret(KEY, tenant="catbooks") == DUMMY  # 승인 전에는 그대로
    listed = client.get(ADMIN, headers=people["a-2"]).json()["data"]
    req = listed["requests"][0]
    assert req["id"] == req_id and req["requested_by"] == "민지" and req["hint"] == DUMMY2[-4:]
    assert listed["approval"] == {"needed": True, "admins": 2}
    # 요청한 사람은 스스로 승인하지 못한다
    own = client.post(f"{ADMIN}/requests/{req_id}/approve", headers=people["a-1"])
    assert own.status_code == 403 and own.json()["error"]["code"] == "SELF_APPROVAL"
    ok = client.post(f"{ADMIN}/requests/{req_id}/approve", headers=people["a-2"])
    assert ok.status_code == 200, ok.text
    assert resolver.get_secret(KEY, tenant="catbooks") == DUMMY2
    assert client.get(ADMIN, headers=people["a-2"]).json()["data"]["requests"] == []


def test_delete_waits_and_reject_drops_the_request(client: TestClient, people: Any) -> None:
    _put_key(client, people["a-1"])
    res = client.post(f"{ADMIN}/keys/{KEY}/delete", json={"reason": "정리"}, headers=people["a-1"])
    assert res.status_code == 200 and res.json()["data"]["applied"] is False
    req_id = res.json()["data"]["request"]["id"]
    no = client.post(f"{ADMIN}/requests/{req_id}/reject", headers=people["a-2"])
    assert no.status_code == 200
    assert resolver.get_secret(KEY, tenant="catbooks") == DUMMY
    assert client.get(ADMIN, headers=people["a-1"]).json()["data"]["requests"] == []
    gone = client.post(f"{ADMIN}/requests/{req_id}/approve", headers=people["a-2"])
    assert gone.status_code == 404


def test_delete_method_works_too(client: TestClient, people: Any) -> None:
    _put_key(client, people["a-1"])
    res = client.delete(f"{ADMIN}/keys/{KEY}", headers=people["a-1"])
    assert res.status_code == 200 and res.json()["data"]["op"] == "delete"


def test_single_admin_applies_at_once_and_everyone_is_told(client: TestClient, env: Path) -> None:
    solo = _people(env, [ROWS[0], ROWS[2]])
    _put_key(client, solo["a-1"])
    res = _put_key(client, solo["a-1"], DUMMY2)
    data = res.json()["data"]
    assert data["applied"] is True and data["op"] == "replace"
    assert resolver.get_secret(KEY, tenant="catbooks") == DUMMY2
    notices = client.get(f"{VIEW}/status", headers=solo["a-3"]).json()["data"]["notices"]
    top = notices[0]
    assert top["action"] == "key_replace" and top["by"] == "민지" and top["hint"] == DUMMY2[-4:]
    assert top["single_admin"] is True
    deleted = client.post(f"{ADMIN}/keys/{KEY}/delete", json={}, headers=solo["a-1"])
    assert deleted.json()["data"]["applied"] is True
    assert resolver.get_secret(KEY, tenant="catbooks") == ""


def test_secret_store_off_blocks_saving_with_a_reason(
    client: TestClient, people: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AX_SECRET_STORE", "env")
    got = client.get(ADMIN, headers=people["a-1"]).json()["data"]
    assert got["secret_store"]["enabled"] is False and got["secret_store"]["reason"]
    assert got["can_save"] is False
    res = _put_key(client, people["a-1"])
    assert res.status_code == 409 and res.json()["error"]["code"] == "SECRET_STORE_DISABLED"
    res = client.post(f"{ADMIN}/settings", json={"model": "m"}, headers=people["a-1"])
    assert res.status_code == 409 and res.json()["error"]["code"] == "SECRET_STORE_DISABLED"


def test_no_company_yet_says_so(
    client: TestClient, people: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(tenants.TENANT_ENV, "")
    status = client.get(f"{VIEW}/status", headers=people["a-3"]).json()["data"]
    assert status["state"] == "no_company" and status["company"] is None
    res = _put_key(client, people["a-1"])
    assert res.status_code == 409 and res.json()["error"]["code"] == "NO_COMPANY"


# ---- 설정 ----


def test_settings_apply_at_once_and_members_are_told(client: TestClient, people: Any) -> None:
    body = {
        "model": "company-model",
        "monthly_budget_tokens": 1000,
        "coder": {"mode": "separate", "model": "coder-model"},
        "video": {"provider": "seedance", "enabled": True},
    }
    res = client.post(f"{ADMIN}/settings", json=body, headers=people["a-1"])
    assert res.status_code == 200, res.text
    s = res.json()["data"]["settings"]
    assert s["model"] == "company-model" and s["monthly_budget_tokens"] == 1000
    assert s["coder"]["mode"] == "separate" and s["updated_by"] == "민지"
    got = client.get(ADMIN, headers=people["a-2"]).json()["data"]["settings"]
    assert got["model"] == "company-model"
    notices = client.get(f"{VIEW}/status", headers=people["a-3"]).json()["data"]["notices"]
    assert notices[0]["action"] == "settings"
    assert set(notices[0]["changed"]) >= {"model", "monthly_budget_tokens", "coder", "video"}
    # PUT도 같다
    res = client.put(f"{ADMIN}/settings", json={**body, "model": "other"}, headers=people["a-1"])
    assert res.status_code == 200 and res.json()["data"]["settings"]["model"] == "other"


@pytest.mark.parametrize(
    "body,code",
    [
        ({"provider": "openai_compatible"}, "BASE_URL_REQUIRED"),
        ({"base_url": "ftp://x.test"}, "INVALID_URL"),
        ({"base_url": "https://user:pw@x.test/v1"}, "INVALID_URL"),
        ({"coder": {"mode": "separate"}}, "CODER_MODEL_REQUIRED"),
        ({"coder": {"mode": "separate", "model": "c", "base_url": "nope"}}, "INVALID_URL"),
    ],
)
def test_settings_validation(client: TestClient, people: Any, body: dict, code: str) -> None:
    res = client.post(f"{ADMIN}/settings", json={"model": "m", **body}, headers=people["a-1"])
    assert res.status_code == 422 and res.json()["error"]["code"] == code


def test_settings_reject_unknown_fields(client: TestClient, people: Any) -> None:
    res = client.post(
        f"{ADMIN}/settings", json={"model": "m", "api_key": DUMMY}, headers=people["a-1"]
    )
    assert res.status_code == 422 and DUMMY not in res.text


# ---- 상태·사용량 ----


def test_status_states(client: TestClient, people: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    member = people["a-3"]
    s = client.get(f"{VIEW}/status", headers=member).json()["data"]
    assert s["state"] == "no_key" and s["company"]["id"] == "catbooks"
    assert s["trial"]["available"] is False
    monkeypatch.setenv(company_ai.TRIAL_KEY_ENV, "DUMMY-TRIAL-KEY-0000")
    monkeypatch.setenv(company_ai.TRIAL_TOKENS_ENV, "100")
    s = client.get(f"{VIEW}/status", headers=member).json()["data"]
    assert s["state"] == "trial" and s["trial"]["limit_tokens"] == 100
    assert "DUMMY-TRIAL-KEY-0000" not in json.dumps(s)
    # 체험 사용량은 그 회사로 센다 (예전 기록의 기본 회사 값 shop-demo는 catbooks로 본다)
    _usage("shop-demo", "hr", 60, 40, datetime(2026, 1, 5, tzinfo=UTC))
    _usage("studyon", "hr", 500, 500, datetime.now(UTC))  # 다른 회사는 섞이지 않는다
    s = client.get(f"{VIEW}/status", headers=member).json()["data"]
    assert s["state"] == "trial_exhausted" and s["trial"]["used_tokens"] == 100
    _put_key(client, people["a-1"])
    s = client.get(f"{VIEW}/status", headers=member).json()["data"]
    assert s["state"] == "connected" and s["key"] == {"has_key": True}
    client.post(
        f"{ADMIN}/settings", json={"model": "m", "monthly_budget_tokens": 50}, headers=people["a-1"]
    )
    _usage("catbooks", "finance", 30, 30, datetime.now(UTC))
    s = client.get(f"{VIEW}/status", headers=member).json()["data"]
    assert s["state"] == "budget_exhausted"
    assert s["usage"]["used_tokens"] == 60 and s["usage"]["percent"] == 120.0
    assert s["model"] == "m"


def test_usage_by_department_with_percent(client: TestClient, people: Any) -> None:
    client.post(
        f"{ADMIN}/settings",
        json={"model": "m", "monthly_budget_tokens": 1000},
        headers=people["a-1"],
    )
    now = datetime.now(UTC)
    _usage("catbooks", "hr", 100, 50, now)
    _usage("catbooks", "hr", 30, 20, now)
    _usage("shop-demo", "finance", 200, 0, now)
    _usage("studyon", "finance", 999, 999, now)
    data = client.get(f"{VIEW}/usage", headers=people["a-3"]).json()["data"]
    assert data["month"] == now.strftime("%Y-%m") and data["recording"] is True
    assert data["total_tokens"] == 400 and data["budget_tokens"] == 1000
    assert data["percent"] == 40.0
    rows = {d["department"]: d for d in data["departments"]}
    assert rows["hr"]["total_tokens"] == 200 and rows["hr"]["calls"] == 2
    assert rows["hr"]["percent_of_budget"] == 20.0 and rows["hr"]["share_percent"] == 50.0
    assert rows["finance"]["input_tokens"] == 200
    # 많이 쓴 순, 같으면 이름 순
    assert [d["department"] for d in data["departments"]] == ["finance", "hr"]
    old = client.get(f"{VIEW}/usage?month=2020-01", headers=people["a-3"]).json()["data"]
    assert old["total_tokens"] == 0 and old["departments"] == []
    bad = client.get(f"{VIEW}/usage?month=2026-13", headers=people["a-3"])
    assert bad.status_code == 422


def test_usage_pages_past_the_query_limit(
    client: TestClient, people: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(company_ai, "PAGE", 3)
    now = datetime.now(UTC)
    for i in range(8):
        _usage("catbooks", "hr", 1, 0, now.replace(microsecond=i))
    data = client.get(f"{VIEW}/usage", headers=people["a-3"]).json()["data"]
    assert data["total_tokens"] == 8 and data["departments"][0]["calls"] == 8


def test_usage_recording_off(
    client: TestClient, people: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm_usage.reset_store()
    monkeypatch.delenv(llm_usage.DB_ENV, raising=False)
    data = client.get(f"{VIEW}/usage", headers=people["a-3"]).json()["data"]
    assert data["recording"] is False and data["total_tokens"] == 0
    s = client.get(f"{VIEW}/status", headers=people["a-3"]).json()["data"]
    assert s["usage"]["recording"] is False


# ---- 월 예산 상한 %(11/20) ----


def test_limit_percent_is_saved_logged_and_told(client: TestClient, people: Any) -> None:
    """상한 %도 모델·예산 변경과 같다: 바로 적용, 이력에 남고 회원에게 알린다."""
    body = {"model": "m", "monthly_budget_tokens": 1000, "monthly_limit_percent": 80}
    res = client.post(f"{ADMIN}/settings", json=body, headers=people["a-1"])
    assert res.status_code == 200, res.text
    assert res.json()["data"]["settings"]["monthly_limit_percent"] == 80
    assert "monthly_limit_percent" in res.json()["data"]["changed"]
    notices = client.get(f"{VIEW}/status", headers=people["a-3"]).json()["data"]["notices"]
    assert "monthly_limit_percent" in notices[0]["changed"]
    history = client.get(ADMIN, headers=people["a-2"]).json()["data"]["history"]
    assert "monthly_limit_percent" in history[0]["changed"]
    for bad in (0, 101):
        res = client.post(
            f"{ADMIN}/settings", json={**body, "monthly_limit_percent": bad}, headers=people["a-1"]
        )
        assert res.status_code == 422


def test_status_stops_at_the_limit_percent(client: TestClient, people: Any) -> None:
    _put_key(client, people["a-1"])
    body = {"model": "m", "monthly_budget_tokens": 100, "monthly_limit_percent": 50}
    client.post(f"{ADMIN}/settings", json=body, headers=people["a-1"])
    _usage("catbooks", "finance", 20, 29, datetime.now(UTC))
    s = client.get(f"{VIEW}/status", headers=people["a-3"]).json()["data"]
    assert s["state"] == "connected"
    assert s["usage"]["limit_percent"] == 50 and s["usage"]["limit_tokens"] == 50
    _usage("catbooks", "finance", 1, 0, datetime.now(UTC))
    s = client.get(f"{VIEW}/status", headers=people["a-3"]).json()["data"]
    assert s["state"] == "budget_exhausted" and s["usage"]["percent"] == 50.0
    u = client.get(f"{VIEW}/usage", headers=people["a-3"]).json()["data"]
    assert u["limit_percent"] == 50 and u["limit_tokens"] == 50


def test_no_limit_percent_means_the_whole_budget(client: TestClient, people: Any) -> None:
    _put_key(client, people["a-1"])
    client.post(
        f"{ADMIN}/settings", json={"model": "m", "monthly_budget_tokens": 100},
        headers=people["a-1"],
    )
    _usage("catbooks", "finance", 50, 49, datetime.now(UTC))
    s = client.get(f"{VIEW}/status", headers=people["a-3"]).json()["data"]
    assert s["state"] == "connected" and s["usage"]["limit_percent"] == 100
    assert s["usage"]["limit_tokens"] == 100
