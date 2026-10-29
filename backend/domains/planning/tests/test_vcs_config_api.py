"""개발 도구 연결 화면용 형상관리 설정 API. 토큰은 돌려주지 않는다."""

from fastapi.testclient import TestClient

from planning_agent import vcs
from planning_agent.main import app


def setup_function() -> None:
    vcs._screen = None
    vcs.set_vcs(None)


def teardown_function() -> None:
    vcs._screen = None
    vcs.set_vcs(None)


def test_default_is_mock_and_test_passes() -> None:
    c = TestClient(app)
    d = c.get("/api/planning/vcs").json()["data"]
    assert d["kind"] == "mock" and d["token_set"] is False
    t = c.post("/api/planning/vcs/test").json()["data"]
    assert t["ok"] is True and t["kind"] == "mock"


def test_save_github_hides_token_and_keeps_it_on_blank() -> None:
    c = TestClient(app)
    secret = "ghp_secretvalue1234"
    r = c.post("/api/planning/vcs", json={"kind": "github", "repo": "acme/shop", "token": secret})
    d = r.json()["data"]
    assert r.status_code == 200 and d["kind"] == "github" and d["token_set"]
    assert secret not in r.text and d["token_hint"].endswith("1234")
    # 토큰 칸을 비워 다시 저장해도 이전 토큰을 쓴다
    d2 = c.post("/api/planning/vcs", json={"kind": "github", "repo": "acme/web"}).json()["data"]
    assert d2["repo"] == "acme/web" and d2["token_set"]


def test_bad_config_rejected_and_previous_kept() -> None:
    c = TestClient(app)
    r = c.post(
        "/api/planning/vcs", json={"kind": "github", "repo": "not-a-repo", "token": "x" * 10}
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_VCS"
    assert c.get("/api/planning/vcs").json()["data"]["kind"] == "mock"
    r = c.post("/api/planning/vcs", json={"kind": "svn"})
    assert r.status_code == 400
