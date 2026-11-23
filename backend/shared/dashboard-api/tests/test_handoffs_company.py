"""handoffs는 요청 회사 것만 본다. 옛 행(shop-demo)은 캣북스 것으로 이어서 보인다 (5단계)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import human_auth
from ax_contracts.schemas.handoff import HandoffCreate
from ax_dashboard_api import handoffs
from ax_dashboard_api.main import app


def _task(tenant: str, key: str) -> HandoffCreate:
    return HandoffCreate(
        tenant_id=tenant,
        context_id=f"ctx_{key}",
        from_service="support",
        to_service="human",
        skill="manual_refund",
        reason=f"확인 필요 {key}",
        assignee_unit="support",
        idempotency_key=key,
    )


def _session(company: str) -> dict[str, str]:
    token, _ = human_auth.issue_session(
        account_id=f"acc_{company}",
        name=company,
        role="admin",
        company_id=company,
        company_role="admin",
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AX_SESSION_SECRET", "test-secret-" + "x" * 32)
    monkeypatch.setenv(handoffs.DB_ENV, str(tmp_path / "handoffs.db"))
    monkeypatch.delenv("TENANT_ID", raising=False)
    handoffs.reset_store()
    store = handoffs.get_store()
    store.add(_task("shop-demo", "old"))  # 옛 이름으로 저장된 캣북스 행
    store.add(_task("catbooks", "new"))  # 정식 id로 저장된 캣북스 행
    store.add(_task("nubitour", "other"))
    yield TestClient(app)
    handoffs.reset_store()


def _ids(client: TestClient, company: str) -> set[str]:
    res = client.get("/api/dashboard/handoffs", headers=_session(company))
    assert res.status_code == 200, res.text
    return {t["context_id"] for t in res.json()["data"]}


def test_catbooks_sees_old_and_new_names_but_not_others(client: TestClient) -> None:
    assert _ids(client, "catbooks") == {"ctx_old", "ctx_new"}


def test_other_company_sees_only_its_own(client: TestClient) -> None:
    assert _ids(client, "nubitour") == {"ctx_other"}


def test_third_company_sees_nothing(client: TestClient) -> None:
    assert _ids(client, "studyon") == set()


def test_cannot_close_another_companys_task(client: TestClient) -> None:
    other = next(t for t in handoffs.get_store().list("nubitour"))
    res = client.post(
        f"/api/dashboard/handoffs/{other.task_id}/complete",
        headers=_session("catbooks"),
        json={"status": "done", "result": "x"},
    )
    assert res.status_code == 404  # 없는 것처럼
