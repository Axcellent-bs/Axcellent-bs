"""도구 연결 보관함은 로그인 세션 회사 것만 (11/25 회사 분리 남은 것).

`/api/dashboard/tool-connections`가 회사를 거는 화면 경로(`SCREEN_PREFIXES`)에 없어서, 누비투어로
로그인해도 캣북스 보관함(GitHub 앱·배포 레포·젠킨스·DejaFix)을 읽었다.

- 캣북스 세션(과 회사를 모르는 예전 토큰)은 예전 파일 그대로
- 누비투어 세션은 그 회사 폴더 파일만 본다(없으면 빈 목록). 누비투어가 저장한 연결은 캣북스에
  안 보인다
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import human_auth
from ax_agent_template import tool_connections as store
from ax_agent_template.companies import COMPANIES_DIR_ENV
from ax_dashboard_api import tool_connections as routes
from ax_dashboard_api.company_connection import Check
from ax_dashboard_api.main import app

BASE = "/api/dashboard/tool-connections"


def _session(company: str) -> dict[str, str]:
    token, _ = human_auth.issue_session(account_id=f"acc-{company}", name="관리자", role="admin",
                                        company_id=company, company_role="admin")  # fmt: skip
    return {"Authorization": f"Bearer {token}"}


def _body(name: str) -> dict:
    return {"kind": "jenkins", "name": name,
            "config": {"url": "http://10.20.30.40:8080", "username": "bot", "job": "deploy",
                       "service": "shop-web"},
            "secrets": {"api_token": "placeholder-token"}}  # fmt: skip


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(human_auth.SESSION_SECRET_ENV, "s" * 40)  # 테스트용 자리값
    monkeypatch.setenv(store.FILE_ENV, str(tmp_path / "tool_connections.json"))
    monkeypatch.setenv(COMPANIES_DIR_ENV, str(tmp_path / "companies"))
    monkeypatch.setattr(store, "_cache", None)

    async def fake(kind, config, secrets, *, lookup):  # noqa: ANN001, ANN202
        return [Check("job", "작업", True, "읽었어요")]

    monkeypatch.setattr(routes, "run_checks", fake)
    return TestClient(app)


def _names(client: TestClient, headers: dict[str, str]) -> list[str]:
    res = client.get(BASE, headers=headers)
    assert res.status_code == 200
    return [c["name"] for c in res.json()["data"]["connections"]]


def test_other_company_session_does_not_see_catbooks_tools(client: TestClient) -> None:
    catbooks, nubi = _session("catbooks"), _session("nubi")
    assert client.post(BASE, json=_body("캣북스 젠킨스"), headers=catbooks).status_code == 200
    assert _names(client, catbooks) == ["캣북스 젠킨스"]
    assert _names(client, nubi) == []  # 캣북스 보관함을 읽지 않는다
    assert client.post(BASE, json=_body("누비 젠킨스"), headers=nubi).status_code == 200
    assert _names(client, nubi) == ["누비 젠킨스"]
    assert _names(client, catbooks) == ["캣북스 젠킨스"]  # 누비투어 것은 캣북스에 안 보인다
