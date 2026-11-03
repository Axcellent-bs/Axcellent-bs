"""고객사 API 키(BYOK)와 서버 AI 월 예산.

- 고객사 키를 넣으면 코딩 에이전트가 그 키로 부르고, 체험 한도 대신 월 예산만 본다.
- 키 값은 화면·응답으로 돌려주지 않는다 (넣었는지와 끝 4자리만).
"""

from __future__ import annotations

import asyncio
import os
import stat

import pytest
from fastapi.testclient import TestClient

from ax_workflow.core import ActionError
from ax_workflow.models import CodingRun
from dev_agent import coder, llm_key
from dev_agent.main import app

pytestmark = pytest.mark.role("dev")

KEY = "sk-company-test-key-0000-1234"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PLANNING_LLM_KEY_STORE", raising=False)
    llm_key.clear()
    yield
    llm_key.clear()
    coder.reset_llm()


def test_key_is_validated_and_never_shown() -> None:
    with pytest.raises(llm_key.LlmKeyError):
        llm_key.set_key("키 값")
    with pytest.raises(llm_key.LlmKeyError):
        llm_key.set_key(KEY, base_url="ftp://x")
    with pytest.raises(llm_key.LlmKeyError):
        llm_key.set_key(None)  # 처음에는 키가 있어야 한다
    llm_key.set_key(KEY, model="big-model")
    pub = llm_key.public()
    assert pub == {"key_set": True, "key_hint": "••••1234", "base_url": "", "model": "big-model"}
    assert KEY not in repr(llm_key._key)
    llm_key.set_key(None, base_url="https://llm.example")  # 키를 비우면 이전 키 그대로
    assert llm_key._key is not None and llm_key._key.api_key == KEY


def test_coder_uses_company_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANNING_LLM_MODEL", "server-model")
    monkeypatch.setenv("PLANNING_LLM_BASE_URL", "https://server.example")
    monkeypatch.setenv("PLANNING_LLM_API_KEY", "server-key")
    coder.reset_llm()
    assert coder.get_llm().settings.api_key == "server-key"
    llm_key.set_key(KEY, model="company-model")
    got = coder.get_llm().settings
    assert (got.api_key, got.model, got.base_url) == (
        KEY,
        "company-model",
        "https://server.example",
    )


def test_budget_blocks_server_ai_and_company_key_skips_trial(service, monkeypatch) -> None:
    month = service.month_key()
    monkeypatch.setenv("PLANNING_TRIAL_TOKENS", "100")
    t = service.state.tasks[0]
    t.coding = CodingRun(status="merged", updated_at=service.clock(), mode="agent", llm_tokens=100)
    assert service.coding_choice(service.state)[0] == "subscription"  # 체험 한도 다 씀
    llm_key.set_key(KEY)
    assert service.coding_choice(service.state) == (
        "agent", "본인 Claude 연결 없음 → 서버 AI (고객사 키)",
    )  # fmt: skip
    run(service.set_budget(500))
    service.state.usage_by_month[month] = 500
    mode, why = service.coding_choice(service.state)
    assert mode == "subscription" and "이번 달 서버 AI 예산을 다 씀 (500/500 토큰)" in why
    with pytest.raises(ActionError) as err:
        run(service.create_dev_task(title="배너", plan="배너 문구 변경"))
    assert err.value.code == "BUDGET_EXHAUSTED"
    with pytest.raises(ActionError):
        run(service.set_budget(-1))
    s = service.coding_settings()
    assert s["budget"] == {"month": month, "limit": 500, "used": 500}
    assert s["company_key"]["key_set"] and KEY not in str(s)


def test_company_key_api_is_admin_only(service, monkeypatch) -> None:
    monkeypatch.setenv("AX_AGENT_TOKENS", "admin=admin-test-token")
    monkeypatch.setenv("SAFETY_HUMAN_TOKENS", "operator:human-test-token")
    c = TestClient(app)
    admin = {"Authorization": "Bearer admin-test-token"}
    human = {"Authorization": "Bearer human-test-token"}
    url = "/api/planning/coding-settings/company-key"
    assert c.post(url, json={"api_key": KEY}, headers=human).status_code == 403
    r = c.post(url, json={"api_key": KEY}, headers=admin)
    assert r.status_code == 200 and KEY not in r.text
    assert r.json()["data"]["company_key"]["key_hint"] == "••••1234"
    r = c.post("/api/planning/coding-settings", json={"budget_tokens": 2000}, headers=admin)
    assert r.json()["data"]["budget"]["limit"] == 2000
    r = c.post(f"{url}/delete", headers=admin)
    assert r.json()["data"]["company_key"]["key_set"] is False


@pytest.mark.skipif(os.name == "nt", reason="파일 권한 비트는 POSIX에서만 본다")
def test_saved_key_file_is_readable_by_the_owner_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:  # noqa: ANN001
    """저장한 키 파일은 umask가 느슨해도, 예전 임시 파일이 남아 있어도 0600이다."""
    path = tmp_path / "llm_key.json"
    monkeypatch.setenv("PLANNING_LLM_KEY_STORE", str(path))
    old_umask = os.umask(0o002)  # 서버와 같은 umask
    try:
        stale = tmp_path / "llm_key.json.tmp"
        stale.write_text("old")
        stale.chmod(0o666)  # 이전에 느슨한 권한으로 남은 임시 파일
        llm_key.set_key(KEY, model="big-model")
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert llm_key.load_saved() and llm_key.public()["key_set"] is True
