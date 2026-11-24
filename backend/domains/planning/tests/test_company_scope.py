"""기획 화면·skill은 회사마다 따로 (11/24 누비투어 연결).

누비 관리자 화면에 캣북스 제안·기획서가 섞였다.

- 누비투어 로그인 세션으로 기획 화면 GET 경로를 다 불러도 캣북스 건이 0
- 누비투어 skill 요청(meta.tenant_id=nubi)으로 받은 제안은 누비 서비스에만, 캣북스 화면에는 없음
- 다른 회사 서비스는 빈 상태로 시작하고 상태 파일을 따로 둔다. 캣북스·회사 모르는 요청은 예전 그대로
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_scope
from ax_agent_template import human_auth as ha
from ax_contracts.schemas import ExecuteDecision
from ax_contracts.schemas.planning import ReceiveInsightInput
from ax_workflow import store
from planning_agent import runtime
from planning_agent.domain.service import PlanningService
from planning_agent.main import app

pytestmark = pytest.mark.role("planning")

BASE = "/api/planning"
GETS = ["state", "requests", "specs", "tasks", "pull-requests", "threads", "decisions",
        "incident-links", "docs", "review-queue", "projects", "release-checks"]  # fmt: skip


async def _gate(action, params, meta, evidence) -> ExecuteDecision:  # noqa: ANN001
    return ExecuteDecision(execution_id="exe_ok", grade="A", status="executed")


def _insight(n: str) -> ReceiveInsightInput:
    return ReceiveInsightInput.model_validate({
        "insight_id": f"ins-{n}", "title": f"{n} 여행 숏츠", "summary": "검색이 오른다",
        "skill": "trend_history",
        "facts": [{"label": "검색", "value": "1.5배", "source": "trend_history"}],
    })  # fmt: skip


def _session(company: str) -> dict[str, str]:
    token, _ = ha.issue_session(account_id=f"acc-{company}", name="관리자", role="admin",
                                company_id=company, company_role="admin")  # fmt: skip
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def services(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    monkeypatch.setenv(ha.SESSION_SECRET_ENV, "s" * 40)  # 테스트용 자리값
    monkeypatch.delenv(store.ENV_DIR, raising=False)
    old_main, old_by = runtime._service, dict(runtime._by_company)
    cat = runtime.set_service(PlanningService(gate=_gate, relater=None, real_example=True))
    runtime._by_company.clear()
    with company_scope.using("nubi"):
        nubi = runtime.get_service()  # 누비 서비스 (빈 상태로 시작)
    nubi._gate = _gate
    nubi._relater = None
    asyncio.run(nubi.receive_insight(requester="data", inp=_insight("nubi")))
    asyncio.run(cat.receive_insight(requester="data", inp=_insight("catbooks")))
    yield cat, nubi
    runtime._service, runtime._by_company = old_main, old_by


def test_nubi_session_sees_no_catbooks_rows_on_any_planning_screen(services) -> None:  # noqa: ANN001
    cat, nubi = services
    cat_ids = {p.id for p in cat.state.proposals} | {s.id for s in cat.state.specs}
    cat_ids |= {r.id for r in cat.state.requests} | {t.id for t in cat.state.tasks}
    assert cat_ids  # 캣북스 쪽에는 예시·받은 제안이 있다
    client = TestClient(app)
    headers = _session("nubi")
    for path in GETS:
        res = client.get(f"{BASE}/{path}", headers=headers)
        assert res.status_code == 200, path
        text = res.text
        assert "catbooks 여행 숏츠" not in text, path
    state = client.get(f"{BASE}/state", headers=headers).json()["data"]
    assert [p["title"] for p in state["proposals"]] == ["nubi 여행 숏츠"]
    assert state["specs"] == [] and state["requests"] == [] and state["tasks"] == []


def test_catbooks_session_and_anonymous_stay_on_catbooks(services) -> None:  # noqa: ANN001
    client = TestClient(app)
    for headers in (_session("catbooks"), {}):
        titles = [
            p["title"]
            for p in client.get(f"{BASE}/state", headers=headers).json()["data"]["proposals"]
        ]
        assert "catbooks 여행 숏츠" in titles and "nubi 여행 숏츠" not in titles


def test_skill_request_for_nubi_lands_in_nubi_service(services) -> None:  # noqa: ANN001
    cat, nubi = services
    with company_scope.using("nubi"):
        assert runtime.get_service() is nubi
    with company_scope.using("shop-demo"):  # 별칭은 캣북스
        assert runtime.get_service() is cat
    assert runtime.get_service() is cat  # 요청 밖(스스로 도는 루틴)은 예전 서비스
    assert nubi.state.tenant_id == "nubi"


def test_other_company_has_its_own_state_file(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    monkeypatch.setenv(store.ENV_DIR, str(tmp_path))
    assert store.path_for("planning") == str(tmp_path / "planning_state_planning.json")
    assert store.path_for("planning", "nubi") == str(tmp_path / "planning_state_planning.nubi.json")
    svc = PlanningService(
        gate=_gate, relater=None, real_example=True, tenant_id="nubi", company="nubi"
    )
    assert svc.state.proposals == [] and svc.state.specs == []  # 캣북스 예시를 섞지 않는다
    assert svc._state_path == str(
        tmp_path / "planning_state_planning.nubi.json"
    )  # 캣북스 파일과 따로
