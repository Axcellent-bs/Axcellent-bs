"""로그인 세션 토큰(`axs1.`)으로 재무 쓰기: 통과·위조·로그아웃·공용 토큰·기록 이름."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import human_auth
from finance_agent import handlers
from finance_agent.main import app
from finance_agent.sources import MockSources

SECRET = "s" * 40  # 테스트용 자리값 (실제 키 아님)
CLEAR = "/api/finance/pg/clear"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, fixed_now) -> Iterator[None]:
    monkeypatch.setenv("FINANCE_HUMAN_TOKENS", "공용:tok-shared")
    monkeypatch.delenv("SAFETY_HUMAN_TOKENS", raising=False)
    monkeypatch.delenv("FINANCE_PG_INBOX", raising=False)
    handlers.reset_state(MockSources())
    yield
    handlers.reset_state(MockSources())


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    monkeypatch.setenv(human_auth.SESSION_SECRET_ENV, SECRET)
    monkeypatch.setenv(human_auth.REVOKED_FILE_ENV, str(tmp_path / "revoked.json"))
    monkeypatch.delenv(human_auth.ALLOW_SHARED_ENV, raising=False)
    human_auth.reset_revoked_for_tests()
    return human_auth.issue_session(account_id="acc-1", name="예진", role="admin")[0]


def _clear(token: str) -> Any:
    return TestClient(app).post(
        CLEAR,
        json={"reason": "시연 끝"},
        headers={"Authorization": f"Bearer {token}", "X-Real-IP": "192.168.0.22"},
    )


def test_a_session_token_writes_and_records_the_account_name(session: str) -> None:
    res = _clear(session)
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    # 공용 토큰이 아니라서 화면 표시 이름(by_label)을 붙이지 않는다
    assert data["by"] == "예진" and "by_label" not in data


def test_forged_and_logged_out_session_tokens_are_refused(session: str) -> None:
    prefix, body, sig = session.split(".")
    assert _clear(f"{prefix}.{body}.{'A' * len(sig)}").status_code == 401
    human_auth.revoke_session(human_auth.verify_session(session))
    res = _clear(session)
    assert res.status_code == 401 and "로그아웃" in res.json()["error"]["message"]


def test_shared_token_keeps_its_old_name_until_switched_off(
    session: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = _clear("tok-shared").json()["data"]
    assert data["by"] == "공용" and data["by_label"] == "공용(192.168.0.22)"
    monkeypatch.setenv(human_auth.ALLOW_SHARED_ENV, "0")
    assert _clear("tok-shared").status_code == 401
