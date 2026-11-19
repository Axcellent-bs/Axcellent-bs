"""로그인 세션 토큰(`axs1.`)으로 인사 대화: 통과·위조·로그아웃·공용 토큰·기록 이름."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import human_auth
from hr_agent.chat.turn import ChatOut
from hr_agent.main import app
from hr_agent.services import Services

SECRET = "s" * 40  # 테스트용 자리값 (실제 키 아님)
URL = "/api/hr/chat"


class FakeLLM:
    async def structured(self, prompt: str, schema: type, **_: Any) -> ChatOut:
        return ChatOut(reply="알겠어요.", summary="물어봄")


@pytest.fixture
def client(svc: Services) -> TestClient:
    svc.llm = FakeLLM()
    return TestClient(app)


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    monkeypatch.setenv(human_auth.SESSION_SECRET_ENV, SECRET)
    monkeypatch.setenv(human_auth.REVOKED_FILE_ENV, str(tmp_path / "revoked.json"))
    monkeypatch.delenv(human_auth.ALLOW_SHARED_ENV, raising=False)
    human_auth.reset_revoked_for_tests()
    return human_auth.issue_session(account_id="acc-1", name="예진", role="member")[0]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_a_session_token_talks_and_the_thread_keeps_the_account_name(
    client: TestClient, session: str
) -> None:
    res = client.post(URL, json={"message": "안녕"}, headers=_bearer(session))
    assert res.status_code == 200
    assert res.json()["data"]["who"] == "예진"


def test_a_forged_session_token_is_refused(client: TestClient, session: str) -> None:
    prefix, body, sig = session.split(".")
    forged = f"{prefix}.{body}.{'A' * len(sig)}"
    assert client.get(URL, headers=_bearer(forged)).status_code == 403


def test_a_logged_out_session_token_is_refused(client: TestClient, session: str) -> None:
    human_auth.revoke_session(human_auth.verify_session(session))
    res = client.get(URL, headers=_bearer(session))
    assert res.status_code == 401
    assert "로그아웃" in res.json()["error"]["message"]


def test_shared_token_still_works_with_its_old_name(
    client: TestClient, session: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SAFETY_HUMAN_TOKENS", "공용:shared-secret")
    res = client.post(URL, json={"message": "안녕"}, headers=_bearer("shared-secret"))
    assert res.json()["data"]["who"] == "공용"
    monkeypatch.setenv(human_auth.ALLOW_SHARED_ENV, "0")
    assert client.get(URL, headers=_bearer("shared-secret")).status_code == 403
