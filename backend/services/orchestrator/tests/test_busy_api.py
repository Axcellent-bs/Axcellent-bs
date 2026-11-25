"""GET /api/busy: 진행 중인 의뢰 단계의 건수만 낸다 (인증 없음, 개인정보·번호 없음)."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from ax_orchestrator.main import app, state
from ax_orchestrator.store import IncidentStore
from ax_orchestrator.work_requests import WorkRequests

SECRET_TEXT = "비밀고객문장 010-1234-5678 홍길동"


def plan() -> list[dict]:
    return [
        {"step_id": "trend", "dept": "data", "skill": "trend_report_create", "grade": "read"},
        {"step_id": "confirm", "dept": None, "skill": "human_check", "after": ["trend"]},
        {"step_id": "make", "dept": "marketing", "skill": "generate_media", "grade": "B"},
    ]


def make(reqs: WorkRequests, account: str, **steps: str) -> str:
    row = reqs.create(account, SECRET_TEXT)
    rid = row["request_id"]
    reqs.set_plan(rid, task="promo_video", confidence=0.9, reason="r", alternatives=[], plan=plan())
    for sid, status in steps.items():
        reqs.update_step(rid, sid, status=status)
    return rid


@pytest.fixture
def reqs(tmp_path) -> WorkRequests:
    store = IncidentStore(str(tmp_path / "orch.db"))
    return WorkRequests(store)


@pytest.fixture
def client(reqs, monkeypatch: pytest.MonkeyPatch):
    managers = {"catbooks": SimpleNamespace(work_requests=reqs)}
    monkeypatch.setattr(state, "managers", managers)
    return TestClient(app)


def test_counts_by_state(reqs: WorkRequests) -> None:
    make(reqs, "a", trend="running")
    make(reqs, "b", confirm="waiting_human", make="pending")
    make(reqs, "c", trend="completed", confirm="completed", make="waiting_async")
    make(reqs, "d", make="held")
    make(reqs, "e", trend="completed")  # 일하는 단계 없음
    assert reqs.busy_counts() == {
        "waiting_async": 1,
        "running": 1,
        "held": 1,
        "waiting_human": 1,
        "video_in_flight": 1,
    }


def test_closed_requests_are_not_counted(reqs: WorkRequests) -> None:
    rid = make(reqs, "a", make="waiting_async")
    reqs.set_status(rid, "failed")
    assert reqs.busy_counts()["waiting_async"] == 0


def test_busy_endpoint_shape_and_no_auth(client: TestClient, reqs: WorkRequests) -> None:
    make(reqs, "a", trend="running")
    make(reqs, "c", make="waiting_async")
    make(reqs, "d", make="held")
    res = client.get("/api/busy")  # 토큰 없이도 읽힌다 (/health처럼)
    assert res.status_code == 200
    body = res.json()
    assert body["meta"] == {}
    data = body["data"]
    assert data["in_flight"] == 2  # running + waiting_async
    assert (data["running"], data["waiting_async"]) == (1, 1)
    assert (data["held"], data["waiting_human"], data["video_in_flight"]) == (1, 0, 1)
    assert data["checked_at"]


def test_busy_endpoint_sums_companies_and_empty(client: TestClient, reqs: WorkRequests) -> None:
    other = WorkRequests()
    make(other, "z", make="waiting_async")
    make(reqs, "a", make="waiting_async")
    state.managers["acme"] = SimpleNamespace(work_requests=other)
    assert client.get("/api/busy").json()["data"]["in_flight"] == 2
    state.managers.clear()
    assert client.get("/api/busy").json()["data"]["in_flight"] == 0


def test_busy_endpoint_exposes_only_counts(client: TestClient, reqs: WorkRequests) -> None:
    rid = make(reqs, "member-account", make="waiting_async")
    raw = client.get("/api/busy").text
    for secret in (SECRET_TEXT, "010-1234-5678", "member-account", rid, "promo_video"):
        assert secret not in raw
    data = json.loads(raw)["data"]
    assert all(isinstance(v, int) for k, v in data.items() if k != "checked_at")
