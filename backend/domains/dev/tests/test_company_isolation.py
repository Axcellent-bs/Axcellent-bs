"""개발 화면·레포 연결은 회사마다 따로 (11/25 회사 분리).

누비투어로 로그인해도 개발 현황이 캣북스 것이고, 레포 연결에 캣북스 레포가 자동으로 붙어 있었다.

- 레포 연결: 캣북스(설치 회사)는 환경변수 연결(캣북스 레포) + 화면 연결 그대로. 다른 회사는 자기
  화면 연결만 보고 환경변수 연결은 보지 않는다(연결 안 했으면 빈 목록 = "연결된 레포 없음")
- 다른 회사 세션으로 저장한 연결은 그 회사에만 있고 캣북스 목록에는 없다. 운영자 토큰으로 쓰는
  저장도 `X-AX-Session` 머리의 세션 회사 자리에 들어간다
- 개발 서비스(작업·PR·배포 상태)도 회사마다 따로, 다른 회사는 빈 상태·따로 상태 파일
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_scope
from ax_agent_template import human_auth as ha
from ax_workflow import store
from dev_agent import runtime, vcs
from dev_agent.main import app
from dev_agent.service import DevService

pytestmark = pytest.mark.role("dev")

CONNS = "/api/planning/vcs/connections"
CATBOOKS_REPO = "Catbooks-demo/catbooks-demo"


def _session(company: str) -> str:
    token, _ = ha.issue_session(account_id=f"acc-{company}", name="관리자", role="admin",
                                company_id=company, company_role="admin")  # fmt: skip
    return token


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path):  # noqa: ANN001, ANN201
    monkeypatch.setenv(ha.SESSION_SECRET_ENV, "s" * 40)  # 테스트용 자리값
    monkeypatch.setenv("PLANNING_VCS_KIND", "github")
    monkeypatch.setenv("PLANNING_VCS_REPO", CATBOOKS_REPO)
    monkeypatch.setenv("PLANNING_VCS_TOKEN", "ghp_placeholder_0000")  # 자리값 (부르지 않는다)
    monkeypatch.setenv(vcs.STORE_ENV, str(tmp_path / "vcs_connections.json"))
    monkeypatch.delenv(store.ENV_DIR, raising=False)
    vcs._screen = None
    vcs.set_vcs(None)
    yield
    vcs._screen = None
    vcs.set_vcs(None)


def test_other_company_does_not_get_catbooks_repo(env) -> None:  # noqa: ANN001
    assert [c.repo for c in vcs.connections()] == [CATBOOKS_REPO]  # 설치 회사는 그대로
    with company_scope.using("nubi"):
        assert vcs.connections() == []
        assert vcs.describe()["source"] == "default"  # 연결된 레포 없음
    client = TestClient(app)
    rows = client.get(CONNS, headers={"Authorization": f"Bearer {_session('nubi')}"}).json()
    assert rows["data"] == []
    assert CATBOOKS_REPO not in str(rows)


def test_saved_connection_stays_in_its_company(env, tmp_path) -> None:  # noqa: ANN001
    client = TestClient(app)
    body = {"kind": "github", "repo": "nubi-tour/site", "token": "ghp_nubi_placeholder"}
    res = client.post(CONNS, json=body, headers={"X-AX-Session": _session("nubi")})
    assert res.status_code == 200, res.text
    with company_scope.using("nubi"):
        assert [c.repo for c in vcs.connections()] == ["nubi-tour/site"]
    assert [c.repo for c in vcs.connections()] == [CATBOOKS_REPO]  # 캣북스에는 없다
    assert (tmp_path / "vcs_connections.json.nubi").exists()  # 회사 파일에 따로
    assert not (tmp_path / "vcs_connections.json").exists()
    vcs.set_vcs(None)  # 다시 읽어도 회사 파일에서
    with company_scope.using("nubi"):
        assert [c.repo for c in vcs.connections()] == ["nubi-tour/site"]
    with company_scope.using("studyon"):
        assert vcs.connections() == []


def test_dev_service_is_separate_per_company(env) -> None:  # noqa: ANN001
    old_main, old_by = runtime._service, dict(runtime._by_company)
    try:
        cat = runtime.set_service(DevService(real_example=True))
        runtime._by_company.clear()
        with company_scope.using("nubi"):
            nubi = runtime.get_service()
            assert runtime.get_service() is nubi
        assert nubi is not cat and nubi.company == "nubi" and nubi.tenant_id == "nubi"
        assert cat.state.tasks or cat.state.prs  # 캣북스 쪽은 예시 작업이 있다
        assert not nubi.state.tasks and not nubi.state.prs  # 빈 상태로 시작
        with company_scope.using("catbooks"):
            assert runtime.get_service() is cat
        client = TestClient(app)
        state = client.get(
            "/api/planning/state", headers={"Authorization": f"Bearer {_session('nubi')}"}
        ).json()["data"]
        assert not state["tasks"] and not state["prs"]
    finally:
        runtime._service, runtime._by_company = old_main, old_by
