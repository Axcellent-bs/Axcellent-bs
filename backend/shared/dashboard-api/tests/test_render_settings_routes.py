"""회사별 만들기 설정 API (11/25).

- 보기는 사람 토큰이면 누구나, 저장·되돌리기는 고객사 관리자만 (회원 403)
- 모델·길이·비율이 목록·범위 밖이면 422. 모르는 범위는 막지 않는다
- 회사 이름으로 나눠 저장한다: 다른 회사 값은 읽지도 덮지도 않는다
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_connection as cc
from ax_agent_template import human_auth
from ax_agent_template import render_settings as store
from ax_agent_template import tenant as tenants
from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_dashboard_api import accounts, marketing_client
from ax_dashboard_api.main import app

URL = "/api/dashboard/marketing/render-settings"
ROWS = [
    {"id": "a-1", "name": "민지", "email": "m@x.test", "role": "admin"},
    {"id": "a-3", "name": "회원", "email": "h@x.test", "role": "member"},
]
OPTIONS = {
    "defaults": {
        "image_model": "img_a",
        "video_model": "vid_a",
        "image_credits_ok": True,
        "video_seconds": "plan",
        "aspect": "plan",
    },
    "models": {
        "image": [{"id": "img_a", "name": "A"}, {"id": "img_b", "name": "B"}],
        "video": [
            {"id": "vid_a", "name": "VA", "min_s": 3, "max_s": 10, "aspects": ["9:16", "16:9"]},
            {"id": "vid_unknown", "name": "VU", "min_s": None, "max_s": None, "aspects": []},
        ],
    },
}


class Marketing:
    def __init__(self) -> None:
        self.output: dict[str, Any] = OPTIONS
        self.status = TaskStatus.COMPLETED
        self.calls: list[str] = []

    async def __call__(self, target: str, request: AgentRequest) -> AgentResult:
        self.calls.append(request.skill)
        return AgentResult(meta=request.meta, status=self.status, outcome="x", output=self.output)


@pytest.fixture
def mk(monkeypatch: pytest.MonkeyPatch) -> Marketing:
    fake = Marketing()
    monkeypatch.setattr(marketing_client, "SEND", fake)
    return fake


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mk: Marketing) -> Path:
    monkeypatch.delenv("DASHBOARD_MOCK", raising=False)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "test-token")
    monkeypatch.setenv("DASHBOARD_HUMAN_TOKENS", "공용:shared-test-token")
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(tmp_path / "company_connection.json"))
    monkeypatch.setattr(cc, "_cache", None)
    monkeypatch.setenv(tenants.TENANT_ENV, "catbooks")
    monkeypatch.delenv(tenants.TENANTS_DIR_ENV, raising=False)
    monkeypatch.delenv(store.FILE_ENV, raising=False)
    monkeypatch.delenv(accounts.ACCOUNTS_FILE_ENV, raising=False)
    monkeypatch.delenv(human_auth.REVOKED_FILE_ENV, raising=False)
    monkeypatch.delenv(human_auth.GENERATIONS_FILE_ENV, raising=False)
    monkeypatch.delenv(human_auth.ALLOW_SHARED_ENV, raising=False)
    monkeypatch.setenv(human_auth.SESSION_SECRET_ENV, "s" * 40)
    human_auth.reset_revoked_for_tests()
    (tmp_path / "accounts.json").write_text(json.dumps({"accounts": ROWS}), encoding="utf-8")
    return tmp_path


def _who(account_id: str) -> dict[str, str]:
    row = next(r for r in ROWS if r["id"] == account_id)
    token = human_auth.issue_session(account_id=row["id"], name=row["name"], role=row["role"])
    return {"Authorization": f"Bearer {token[0]}"}


@pytest.fixture
def client(env: Path) -> TestClient:
    return TestClient(app)


def _data(res: Any) -> dict[str, Any]:
    assert res.status_code == 200, res.text
    return res.json()["data"]


def test_read_with_nothing_saved_shows_defaults_and_models(client: TestClient) -> None:
    got = _data(client.get(URL, headers=_who("a-3")))  # 회원도 본다
    assert got["settings"] is None
    assert got["defaults"] == OPTIONS["defaults"] and got["models"] == OPTIONS["models"]
    assert got["can_edit"] is False
    assert _data(client.get(URL, headers=_who("a-1")))["can_edit"] is True


def test_admin_saves_and_the_file_has_only_this_company(client: TestClient, env: Path) -> None:
    path = env / store.FILE
    other = {"companies": {"other": {"image_model": "keep_me"}}}
    path.write_text(json.dumps(other), encoding="utf-8")
    body = {"video_model": "vid_a", "video_seconds": 7, "aspect": "16:9",
            "image_credits_ok": False}  # fmt: skip
    got = _data(client.post(f"{URL}/save", json=body, headers=_who("a-1")))
    assert got["settings"] == {"image_model": None, **body}
    on_disk = json.loads(path.read_text(encoding="utf-8"))["companies"]
    assert on_disk["other"] == {"image_model": "keep_me"}  # 다른 회사 값은 그대로
    assert on_disk["catbooks"]["video_seconds"] == 7
    assert store.saved("other") == {"image_model": "keep_me"}
    assert store.saved("catbooks")["aspect"] == "16:9"
    cleared = _data(client.post(f"{URL}/clear", headers=_who("a-1")))
    assert cleared["settings"] is None
    assert json.loads(path.read_text(encoding="utf-8"))["companies"] == other["companies"]


def test_member_cannot_save_or_clear(client: TestClient) -> None:
    assert client.post(f"{URL}/save", json={}, headers=_who("a-3")).status_code == 403
    assert client.post(f"{URL}/clear", headers=_who("a-3")).status_code == 403


@pytest.mark.parametrize(
    "body",
    [
        {"image_model": "nope"},
        {"video_model": "nope"},
        {"video_model": "vid_a", "video_seconds": 11},
        {"video_model": "vid_a", "video_seconds": 2},
        {"video_model": "vid_a", "aspect": "1:1"},
        {"video_seconds": 0},
        {"video_seconds": "soon"},
        {"aspect": "4:3"},
    ],
)
def test_out_of_range_is_422(client: TestClient, env: Path, body: dict[str, Any]) -> None:
    res = client.post(f"{URL}/save", json=body, headers=_who("a-1"))
    assert res.status_code == 422, res.text
    assert not (env / store.FILE).exists()


def test_unknown_range_is_not_blocked(client: TestClient) -> None:
    body = {"video_model": "vid_unknown", "video_seconds": 40, "aspect": "1:1"}
    assert _data(client.post(f"{URL}/save", json=body, headers=_who("a-1")))["settings"]


def test_plan_values_and_empty_save(client: TestClient) -> None:
    body = {"video_seconds": "plan", "aspect": "plan"}
    got = _data(client.post(f"{URL}/save", json=body, headers=_who("a-1")))
    assert got["settings"]["video_seconds"] == "plan"
    empty = _data(client.post(f"{URL}/save", json={}, headers=_who("a-1")))
    assert empty["settings"] is None  # 다 비우면 서버 설정값으로


def test_read_works_when_marketing_is_down_and_save_does_not(
    client: TestClient, mk: Marketing
) -> None:
    mk.status = TaskStatus.FAILED
    got = _data(client.get(URL, headers=_who("a-3")))
    assert got["models"] == {"image": [], "video": []}  # 지어내지 않는다
    assert client.post(f"{URL}/save", json={}, headers=_who("a-1")).status_code == 502
